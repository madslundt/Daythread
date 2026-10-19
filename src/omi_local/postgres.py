"""PostgreSQL persistence for managed archives.

Archive repositories retain their qmark SQL interface. This adapter translates the
small SQLite dialect used by those repositories into native PostgreSQL tables,
indexes, full text search and transactional triggers. No SQLite file is used for
live managed data. SQLite connections are used only for explicit import/export.
"""

import atexit
import hashlib
import re
import sqlite3
import threading

_IDENTIFIER = re.compile(r"[a-z_][a-z0-9_]{0,62}\Z")
_POOLS = {}
_POOL_LOCK = threading.Lock()


def identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(
            "Database schema must be a lowercase SQL identifier, at most 63 characters"
        )
    return value


def _pool(url):
    from psycopg_pool import ConnectionPool

    with _POOL_LOCK:
        if url not in _POOLS:
            _POOLS[url] = ConnectionPool(
                url,
                open=True,
                min_size=0,
                max_size=4,
                max_idle=60,
                timeout=15,
                max_waiting=16,
                kwargs={"connect_timeout": 10, "prepare_threshold": None},
            )
            atexit.register(_POOLS[url].close)
        return _POOLS[url]


class Row(dict):
    """Both the mapping and positional access used by sqlite3.Row callers."""

    def __iter__(self):
        return iter(self.values())

    def __getitem__(self, key):
        return (
            tuple(self.values())[key] if isinstance(key, (int, slice)) else super().__getitem__(key)
        )


def _row_factory(cursor):
    columns = [column.name for column in (cursor.description or [])]
    return lambda values: Row(zip(columns, values, strict=True))


def _statements(script):
    pending = ""
    for character in script:
        pending += character
        if character == ";" and sqlite3.complete_statement(pending):
            yield pending.strip().rstrip(";")
            pending = ""
    if pending.strip():
        yield pending.strip().rstrip(";")


def _arguments(value):
    """Split SQL function arguments, respecting nested expressions and literals."""
    result, start, depth, quote, position = [], 0, 0, None, 0
    while position < len(value):
        char = value[position]
        if quote:
            if char == quote:
                if position + 1 < len(value) and value[position + 1] == quote:
                    position += 1
                else:
                    quote = None
        elif char in "'\"":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            result.append(value[start:position].strip())
            start = position + 1
        position += 1
    result.append(value[start:].strip())
    return result


def _functions(sql, *, json_value=False):
    """Translate functions whose return types differ between the two databases."""
    pattern = re.compile(
        r"\b(json_extract|json_type|json_remove|json_object|json_each|julianday|max|min|sum)\s*\(",
        re.I,
    )
    position = 0
    while found := pattern.search(sql, position):
        cursor, depth, quote = found.end(), 1, None
        while cursor < len(sql) and depth:
            char = sql[cursor]
            if quote:
                if char == quote:
                    if cursor + 1 < len(sql) and sql[cursor + 1] == quote:
                        cursor += 1
                    else:
                        quote = None
            elif char in "'\"":
                quote = char
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            cursor += 1
        if depth:
            raise ValueError("Unclosed SQL function")
        name = found[1].lower()
        args = [
            _functions(arg, json_value=name == "json_object")
            for arg in _arguments(sql[found.end() : cursor - 1])
        ]
        replacement = None
        if name in {"json_extract", "json_type", "json_remove"}:
            path = args[1].strip("'")
            if not re.fullmatch(r"\$(?:\.[a-zA-Z_][a-zA-Z0-9_]*)+", path):
                raise ValueError("Unsupported archive JSON path")
            components = path[2:].split(".")
            encoded = "ARRAY[" + ",".join("'" + part + "'" for part in components) + "]"
            if name == "json_remove":
                replacement = f"(({args[0]})::jsonb #- {encoded})::text"
            elif name == "json_type":
                expr = f"jsonb_typeof(({args[0]})::jsonb #> {encoded})"
                replacement = f"(CASE {expr} WHEN 'string' THEN 'text' ELSE {expr} END)"
            else:
                operator = "#>" if json_value else "#>>"
                replacement = f"(({args[0]})::jsonb {operator} {encoded})"
                if not json_value and components[-1] in {"part_index", "part_count"}:
                    replacement = f"({replacement})::integer"
                elif not json_value and components[-1] in {"latitude", "longitude"}:
                    replacement = f"({replacement})::double precision"
        elif name == "json_object":
            replacement = "(json_build_object(" + ",".join(args) + "))::text"
        elif name == "json_each":
            replacement = f"jsonb_array_elements_text(({args[0]})::jsonb) AS daythread_json(value)"
        elif name == "sum" and re.search(r"[=<>]|\bIS\b", args[0], re.I):
            replacement = f"sum(({args[0]})::integer)"
        elif name == "julianday":
            replacement = (
                f"(extract(epoch FROM ({args[0]})::timestamptz)::double precision/86400.0)"
            )
        elif name in {"max", "min"} and len(args) > 1:
            replacement = ("greatest" if name == "max" else "least") + "(" + ",".join(args) + ")"
        if replacement is None:
            replacement = name + "(" + ",".join(args) + ")"
        sql = sql[: found.start()] + replacement + sql[cursor:]
        position = found.start() + len(replacement)
    return sql


def _parameters(sql):
    result, quote, position = [], None, 0
    while position < len(sql):
        char = sql[position]
        if char == "%":
            result.append("%%")
        elif quote:
            result.append(char)
            if char == quote:
                if position + 1 < len(sql) and sql[position + 1] == quote:
                    result.append(sql[position + 1])
                    position += 1
                else:
                    quote = None
        elif char in "'\"":
            quote = char
            result.append(char)
        else:
            result.append("%s" if char == "?" else char)
        position += 1
    return "".join(result)


LOCATION_FUNCTION = """
CREATE OR REPLACE FUNCTION location_zone_contains(latitude double precision,
 longitude double precision, encoded text) RETURNS integer LANGUAGE sql IMMUTABLE AS $$
 SELECT CASE WHEN latitude IS NULL OR longitude IS NULL THEN 0 ELSE
 CASE WHEN EXISTS (
  SELECT 1 FROM jsonb_array_elements(encoded::jsonb) p WHERE
  6371008.8 * 2 * asin(sqrt(least(1.0,greatest(0.0,
   power(sin(radians((p->>'latitude')::double precision-latitude)/2),2)
   + cos(radians(latitude))*cos(radians((p->>'latitude')::double precision))
   * power(sin(radians((p->>'longitude')::double precision-longitude)/2),2)
  )))) <= (p->>'radius_m')::double precision+0.000001
 ) THEN 1 ELSE 0 END END
$$
"""


class PostgresDatabase:
    def __init__(self, url: str, schema: str):
        self.schema = identifier(schema)
        self.pool = _pool(url)
        self.tables = {}
        # Shared across service replicas; a collision merely serializes two archives.
        self.lock_key = int.from_bytes(
            hashlib.sha256(schema.encode()).digest()[:8], "big", signed=True
        )
        with self.pool.connection() as db:
            db.execute("SELECT pg_advisory_xact_lock(%s)", (self.lock_key,))
            db.execute(f'CREATE SCHEMA IF NOT EXISTS "{self.schema}"')
            db.execute(f'SET LOCAL search_path TO "{self.schema}", pg_catalog')
            db.execute(LOCATION_FUNCTION)

    def connect(self):
        db = self.pool.getconn()
        try:
            db.row_factory = _row_factory
            db.execute(f'SET LOCAL search_path TO "{self.schema}", pg_catalog')
            return PostgresConnection(self, db)
        except BaseException:
            self.pool.putconn(db)
            raise


class PostgresConnection:
    def __init__(self, database, connection):
        self.database = database
        self.connection = connection
        self._write_locked = False
        self._tables = database.tables
        self._closed = False

    @property
    def in_transaction(self):
        from psycopg.pq import TransactionStatus

        return self.connection.info.transaction_status != TransactionStatus.IDLE

    def __enter__(self):
        return self

    def __exit__(self, kind, error, traceback):
        if kind is None:
            self.connection.commit()
        else:
            self.connection.rollback()

    def close(self):
        if not self._closed:
            self.connection.rollback()
            self.database.pool.putconn(self.connection)
            self._closed = True

    def _write_lock(self):
        if not self._write_locked:
            self.connection.execute("SELECT pg_advisory_xact_lock(%s)", (self.database.lock_key,))
            self._write_locked = True

    def _table(self, name):
        if name not in self._tables:
            columns = self.connection.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position",
                (self.database.schema, name),
            ).fetchall()
            primary = self.connection.execute(
                "SELECT a.attname FROM pg_index i JOIN pg_attribute a "
                "ON a.attrelid=i.indrelid AND a.attnum=ANY(i.indkey) "
                "WHERE i.indrelid=to_regclass(%s) AND i.indisprimary ORDER BY a.attnum",
                (name,),
            ).fetchall()
            self._tables[name] = ([row[0] for row in columns], [row[0] for row in primary])
        return self._tables[name]

    def _translate(self, statement):
        sql = re.sub(r"--[^\n]*", "", statement).strip().rstrip(";")
        sql = re.sub(
            r"lower\(hex\(randomblob\(16\)\)\)",
            "replace(gen_random_uuid()::text,'-','')",
            sql,
            flags=re.I,
        )
        sql = re.sub(
            r"strftime\('[^']*','now'\)",
            "to_char(clock_timestamp() AT TIME ZONE 'UTC',"
            "'YYYY-MM-DD\"T\"HH24:MI:SS.US') || '+00:00'",
            sql,
            flags=re.I,
        )
        sql = re.sub(r"\bBLOB\b", "BYTEA", sql, flags=re.I)
        # SQLite REAL has 64-bit precision; PostgreSQL REAL has only 32 bits,
        # insufficient for epoch timestamps used by the notification outbox.
        sql = re.sub(r"\bREAL\b", "DOUBLE PRECISION", sql, flags=re.I)
        sql = re.sub(
            r"\bINTEGER PRIMARY KEY AUTOINCREMENT\b",
            "BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY",
            sql,
            flags=re.I,
        )
        if re.match(r"CREATE TABLE IF NOT EXISTS (profiles|audio)\b", sql, re.I):
            last = sql.rfind(")")
            sql = sql[:last] + ", rowid BIGINT GENERATED BY DEFAULT AS IDENTITY" + sql[last:]
        # The profile catalogue also relies on SQLite's implicit integer row IDs.
        if re.match(r"CREATE TABLE IF NOT EXISTS profile_events\b", sql, re.I):
            sql = sql.replace(
                "sequence INTEGER PRIMARY KEY",
                "sequence BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY",
            )
        sql = re.sub(
            r"\bIS NOT\s+(?!NULL\b|DISTINCT\b)([A-Za-z_][\w.]*)",
            r"IS DISTINCT FROM \1",
            sql,
            flags=re.I,
        )

        def glob(match):
            pattern = match[2].replace("%", r"\%").replace("_", r"\_")
            return match[1] + "LIKE '" + pattern.replace("*", "%").replace("?", "_") + "'"

        sql = re.sub(r"((?:NOT\s+)?)(?:GLOB)\s+'([^']*)'", glob, sql, flags=re.I)
        sql = _functions(sql)
        insertion = re.match(r"INSERT OR (IGNORE|REPLACE) INTO\s+(\w+)\s*(\([^)]*\))?", sql, re.I)
        if insertion:
            operation, table, supplied = insertion.groups()
            sql = sql.replace(insertion[0], "INSERT INTO " + table + (supplied or "") + " ", 1)
            if operation.upper() == "IGNORE":
                sql += " ON CONFLICT DO NOTHING"
            else:
                columns, primary = self._table(table)
                if not primary:
                    raise ValueError(f"No primary key for archive upsert: {table}")
                written = (
                    [part.strip() for part in supplied[1:-1].split(",")] if supplied else columns
                )
                updates = [
                    f'"{column}"=' + (f'EXCLUDED."{column}"' if column in written else "DEFAULT")
                    for column in columns
                    if column not in primary
                ]
                sql += " ON CONFLICT (" + ",".join(primary) + ") DO "
                sql += "UPDATE SET " + ",".join(updates) if updates else "NOTHING"
        return sql

    def _trigger(self, script):
        parsed = re.fullmatch(
            r"CREATE TRIGGER IF NOT EXISTS\s+(\w+)\s+(BEFORE|AFTER)\s+"
            r"(INSERT|DELETE|UPDATE(?:\s+OF\s+[\w, ]+)?)\s+ON\s+(\w+)\s*"
            r"(?:WHEN\s+(.*?))?\s*BEGIN\s*(.*?)\s*END",
            script,
            re.I | re.S,
        )
        if not parsed:
            raise ValueError("Unsupported archive trigger")
        name, timing, event, table, condition, body = parsed.groups()
        body = ";\n".join(self._translate(statement) for statement in _statements(body)) + ";"
        condition = self._translate(condition) if condition else None
        if condition:
            body = f"IF {condition} THEN\n{body}\nEND IF;"
        return_row = "OLD" if event == "DELETE" else "NEW"
        self.connection.execute(
            f'CREATE OR REPLACE FUNCTION "{name}_fn"() RETURNS trigger LANGUAGE plpgsql AS '
            f"$archive$ BEGIN {body} RETURN {return_row}; END $archive$"
        )
        self.connection.execute(f'DROP TRIGGER IF EXISTS "{name}" ON "{table}"')
        self.connection.execute(
            f'CREATE TRIGGER "{name}" {timing} {event} ON "{table}" '
            f'FOR EACH ROW EXECUTE FUNCTION "{name}_fn"()'
        )

    def execute(self, statement, parameters=None):
        sql = statement.strip().rstrip(";")
        if re.fullmatch(r"BEGIN(?: IMMEDIATE)?", sql, re.I):
            if "IMMEDIATE" in sql.upper():
                self._write_lock()
            return self.connection.cursor()
        if re.match(r"PRAGMA table_info\(", sql, re.I):
            table = re.search(r"\((\w+)\)", sql)[1]
            return self.connection.execute(
                "SELECT ordinal_position-1 AS cid,column_name AS name,data_type AS type,"
                "(is_nullable='NO')::integer AS notnull,column_default AS dflt_value "
                "FROM information_schema.columns WHERE table_schema=%s AND table_name=%s "
                "ORDER BY ordinal_position",
                (self.database.schema, table),
            )
        if sql.upper().startswith("PRAGMA"):
            return self.connection.cursor()
        if "sqlite_master" in sql:
            sql = sql.replace("sqlite_master", "information_schema.tables")
            sql = re.sub(r"\bname=", "table_name=", sql)
            sql += " AND table_schema=current_schema()"
        if "transcript_search MATCH" in sql:
            # Store.search constructs a bounded, quoted OR query from Unicode words.
            marker, close, match, *rest = parameters
            headline = "HighlightAll=true,StartSel=" + marker + ",StopSel=" + close
            sql = sql.replace(
                "highlight(transcript_search,1,?,?)", "ts_headline('simple',f.text,q,?)"
            )
            sql = sql.replace(
                "WHERE transcript_search MATCH ?",
                "CROSS JOIN websearch_to_tsquery('simple',?) q "
                "WHERE to_tsvector('simple',f.text) @@ q",
            )
            sql = sql.replace(
                "bm25(transcript_search)", "-ts_rank_cd(to_tsvector('simple',f.text),q)"
            )
            parameters = (headline, match, *rest)
        if re.match(r"(?:INSERT|UPDATE|DELETE|ALTER|CREATE|DROP)\b", sql, re.I):
            self._write_lock()
        if re.match(r"ALTER TABLE", sql, re.I):
            self._tables.clear()
        if re.match(r"CREATE VIRTUAL TABLE", sql, re.I):
            return self.connection.execute(
                "CREATE TABLE IF NOT EXISTS transcript_search(id TEXT PRIMARY KEY,text TEXT)"
            )
        sql = self._translate(sql)
        try:
            if parameters is None:
                return self.connection.execute(sql)
            return self.connection.execute(_parameters(sql), parameters)
        except Exception as exc:
            # Preserve the repository's existing public validation behavior.
            from psycopg import IntegrityError

            if isinstance(exc, IntegrityError):
                raise sqlite3.IntegrityError(str(exc)) from exc
            raise

    def executemany(self, statement, values):
        for parameters in values:
            self.execute(statement, parameters)

    def iterate(self, statement, parameters, batch_size=128):
        """Stream large read-only scans instead of buffering the complete archive."""
        from uuid import uuid4

        sql = _parameters(self._translate(statement))
        with self.connection.cursor(name="archive_" + uuid4().hex) as cursor:
            cursor.execute(sql, parameters)
            while rows := cursor.fetchmany(batch_size):
                yield from rows

    def executescript(self, script):
        self._write_lock()
        for statement in _statements(script):
            statement = re.sub(r"--[^\n]*", "", statement).strip()
            if not statement:
                continue
            if re.match(r"CREATE TRIGGER", statement, re.I):
                self._trigger(statement)
            else:
                self.execute(statement)
        # Native GIN index makes candidate retrieval independent of archive scans.
        if "transcript_search" in script:
            self.connection.execute(
                "CREATE INDEX IF NOT EXISTS transcript_search_lexemes ON transcript_search "
                "USING gin(to_tsvector('simple',text))"
            )

    def backup(self, target):
        from .postgres_transfer import backup

        backup(self, target)

    def import_sqlite(self, source):
        from .postgres_transfer import import_sqlite

        import_sqlite(self, source)
