"""Atomic import and portable SQLite export for native PostgreSQL archives."""

import sqlite3


def backup(archive, target):
    """Materialize an explicit portable export in the existing backup format."""
    from pathlib import Path
    from tempfile import TemporaryDirectory

    from .store import Store

    with TemporaryDirectory() as temporary:
        sqlite_store = Store(Path(temporary), database_url=None)
        with sqlite_store.connect() as schema:
            from .generation_jobs import SCHEMA as GENERATION_SCHEMA

            schema.executescript(GENERATION_SCHEMA)
            schema.backup(target)
    target.execute("PRAGMA foreign_keys=OFF")
    triggers = target.execute("SELECT sql FROM sqlite_master WHERE type='trigger'").fetchall()
    for (name,) in target.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall():
        target.execute(f'DROP TRIGGER "{name}"')
    tables = archive.connection.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema=%s AND table_type='BASE TABLE'",
        (archive.database.schema,),
    ).fetchall()
    for (table,) in [(row[0],) for row in tables]:
        if not target.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone():
            continue
        columns, _ = archive._table(table)
        names = ",".join('"' + column + '"' for column in columns)
        target.execute(f'DELETE FROM "{table}"')
        cursor = archive.connection.execute(f'SELECT {names} FROM "{table}"')
        while rows := cursor.fetchmany(500):
            target.executemany(
                f'INSERT INTO "{table}" ({names}) VALUES ({",".join("?" for _ in columns)})',
                [tuple(row.values()) for row in rows],
            )
    for (trigger,) in triggers:
        target.execute(trigger)
    target.commit()


def import_sqlite(archive, source):
    """Import one existing archive atomically; refuse to overwrite live data."""
    archive._write_lock()
    source_tables = {
        row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    catalog = "audio" not in source_tables and "profiles" in source_tables
    if not catalog and "audio" not in source_tables:
        raise ValueError("Source is not a Daythread archive or profile catalogue")
    if "generation_jobs" in source_tables:
        from .generation_jobs import SCHEMA as GENERATION_SCHEMA

        archive.executescript(GENERATION_SCHEMA)
    # Trigger suppression is transactional and needs only table ownership.
    tables = archive.connection.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema=%s "
        "AND table_type='BASE TABLE'",
        (archive.database.schema,),
    ).fetchall()
    names = [row[0] for row in tables]
    if catalog:
        names = [name for name in names if name in {"profiles", "profile_events"}]
        if (
            archive.execute("SELECT 1 FROM profiles WHERE id!='default' LIMIT 1").fetchone()
            or archive.execute("SELECT 1 FROM profile_events LIMIT 1").fetchone()
        ):
            raise ValueError("PostgreSQL profile catalogue already contains profiles")
    else:
        if archive.execute("SELECT 1 FROM audio LIMIT 1").fetchone():
            raise ValueError("PostgreSQL archive already contains audio")
        seeds = {"label_state", "tag_state", "voice_meta", "profiles", "profile_events"}
        names = [name for name in names if name not in {"profiles", "profile_events"}]
        for table in names:
            if (
                table in source_tables
                and table not in seeds
                and archive.execute(f'SELECT 1 FROM "{table}" LIMIT 1').fetchone()
            ):
                raise ValueError(f"PostgreSQL archive already contains {table}")
    for table in names:
        archive.connection.execute(f'ALTER TABLE "{table}" DISABLE TRIGGER USER')
    source.row_factory = sqlite3.Row
    for table in names:
        if not source.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone():
            continue
        columns, _ = archive._table(table)
        available = {row["name"] for row in source.execute(f'PRAGMA table_info("{table}")')}
        # SQLite rowid determines canonical duplicate ordering. Preserve it even
        # though PRAGMA table_info does not list that implicit column.
        if "rowid" in columns:
            available.add("rowid")
        common = [column for column in columns if column in available]
        encoded = ",".join('"' + column + '"' for column in common)
        archive.connection.execute(f'DELETE FROM "{table}"')
        cursor = source.execute(f'SELECT {encoded} FROM "{table}"')
        while rows := cursor.fetchmany(500):
            placeholders = ",".join("%s" for _ in common)
            archive.connection.cursor().executemany(
                f'INSERT INTO "{table}" ({encoded}) VALUES ({placeholders})',
                [tuple(row) for row in rows],
            )
    for table in names:
        archive.connection.execute(f'ALTER TABLE "{table}" ENABLE TRIGGER USER')
    for row in archive.connection.execute(
        "SELECT table_name,column_name FROM information_schema.columns "
        "WHERE table_schema=%s AND is_identity='YES'",
        (archive.database.schema,),
    ).fetchall():
        table, column = row[0], row[1]
        archive.connection.execute(
            f"SELECT setval(pg_get_serial_sequence('{table}','{column}'),"
            f'coalesce((SELECT max("{column}") FROM "{table}"),0)+1,false)'
        )
