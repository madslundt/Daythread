"""Rebuildable passage index. Source text and versions remain authoritative."""

import hashlib
import heapq
import json
import math
import struct
from datetime import datetime, timedelta
from uuid import uuid4

from .domain import stamp, utc_now

SEMANTIC_SCHEMA = """
CREATE TABLE IF NOT EXISTS embedding_sets (
 id TEXT PRIMARY KEY, manifest TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS embedding_jobs (
 audio_id TEXT NOT NULL, set_id TEXT NOT NULL, version TEXT NOT NULL,
 cursor INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL DEFAULT 'pending',
 retry_at TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(audio_id,set_id)
);
CREATE TABLE IF NOT EXISTS retrieval_passages (
 id TEXT PRIMARY KEY, audio_id TEXT NOT NULL, set_id TEXT NOT NULL, version TEXT NOT NULL,
 ordinal INTEGER NOT NULL, start_offset INTEGER NOT NULL, end_offset INTEGER NOT NULL,
 vector BLOB, UNIQUE(audio_id,set_id,ordinal)
);
CREATE INDEX IF NOT EXISTS passage_set ON retrieval_passages(set_id,audio_id);
CREATE TRIGGER IF NOT EXISTS audio_index_insert AFTER INSERT ON audio BEGIN
 UPDATE audio SET transcript_version=lower(hex(randomblob(16))) WHERE id=NEW.id;
 INSERT OR REPLACE INTO embedding_jobs(audio_id,set_id,version)
 SELECT NEW.id,id,(SELECT transcript_version FROM audio WHERE id=NEW.id)
 FROM embedding_sets WHERE active=1 AND NEW.state='done' AND length(trim(NEW.text))>0;
END;
CREATE TRIGGER IF NOT EXISTS audio_index_update AFTER UPDATE OF text,state ON audio
WHEN OLD.text IS NOT NEW.text OR (OLD.state IS NOT NEW.state AND NEW.state='done') BEGIN
 UPDATE audio SET transcript_version=lower(hex(randomblob(16))) WHERE id=NEW.id;
 DELETE FROM retrieval_passages WHERE audio_id=NEW.id;
 DELETE FROM embedding_jobs WHERE audio_id=NEW.id;
 INSERT INTO embedding_jobs(audio_id,set_id,version)
 SELECT NEW.id,id,(SELECT transcript_version FROM audio WHERE id=NEW.id)
 FROM embedding_sets WHERE active=1 AND NEW.state='done' AND length(trim(NEW.text))>0;
END;
CREATE TRIGGER IF NOT EXISTS audio_index_delete AFTER DELETE ON audio BEGIN
 DELETE FROM retrieval_passages WHERE audio_id=OLD.id;
 DELETE FROM embedding_jobs WHERE audio_id=OLD.id;
END;
"""


def passages(text: str, size: int) -> list[tuple[int, int]]:
    """Character-bounded, overlapping passages; every character is covered, even long imports.

    The operator's character ceiling must fit the chosen model's tokenizer and prefixes.
    No provider-side truncation is requested or relied upon.
    """
    result, start = [], 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            # Prefer sentence/word boundaries without creating tiny passages.
            boundary = max(text.rfind(mark, start + size // 2, end) for mark in (". ", "\n", " "))
            if boundary > start:
                end = boundary + 1
        result.append((start, end))
        if end == len(text):
            break
        start = max(start + 1, end - min(100, size // 8))
    return result


def normalized(vector: list, dimension: int) -> list[float]:
    if (
        not isinstance(vector, list)
        or len(vector) != dimension
        or any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for value in vector
        )
    ):
        raise ValueError("Invalid embedding vector")
    norm = math.hypot(*vector)
    if not math.isfinite(norm) or norm == 0:
        raise ValueError("Invalid embedding norm")
    return [value / norm for value in vector]


class SemanticStore:
    def configure_embeddings(self, manifest: dict | None) -> str | None:
        serialized = json.dumps(manifest, sort_keys=True) if manifest else None
        key = hashlib.sha256(serialized.encode()).hexdigest() if serialized else None
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if manifest is None:
                db.execute("DELETE FROM embedding_staging_passages")
                db.execute("DELETE FROM retrieval_passages")
                db.execute("DELETE FROM embedding_jobs")
                db.execute("DELETE FROM embedding_sets")
                return None
            route_name = (manifest or {}).get("route_name", "default")
            for row in list(db.execute("SELECT id,manifest FROM embedding_sets WHERE active=1")):
                previous = json.loads(row["manifest"])
                if previous.get("route_name", "default") == route_name and row["id"] != key:
                    db.execute("UPDATE embedding_sets SET active=0 WHERE id=?", (row["id"],))
            if key:
                db.execute(
                    "INSERT INTO embedding_sets VALUES(?,?,1) "
                    "ON CONFLICT(id) DO UPDATE SET active=1",
                    (key, serialized),
                )
                # Startup may supersede interrupted work. Every attempt has a distinct
                # token, so a slow pre-restart worker cannot publish into its replacement.
                db.execute(
                    "UPDATE embedding_jobs SET state='pending',lease_token='',lease_until='' "
                    "WHERE set_id=? AND state='working'",
                    (key,),
                )
                db.execute(
                    "INSERT OR IGNORE INTO embedding_jobs(audio_id,set_id,version) "
                    "SELECT id,?,transcript_version FROM audio "
                    "WHERE state='done' AND length(trim(text))>0",
                    (key,),
                )
        return key

    def rebuild_embeddings(self, set_ids: list[str]) -> None:
        """Requeue active contracts without removing their usable, current vectors."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for set_id in set_ids:
                db.execute("DELETE FROM embedding_staging_passages WHERE set_id=?", (set_id,))
                db.execute("DELETE FROM embedding_jobs WHERE set_id=?", (set_id,))
                db.execute(
                    "INSERT INTO embedding_jobs(audio_id,set_id,version,force_reembed) "
                    "SELECT a.id,s.id,a.transcript_version,1 FROM audio a "
                    "JOIN embedding_sets s ON s.id=? AND s.active=1 "
                    "WHERE a.state='done' AND length(trim(a.text))>0", (set_id,),
                )

    def reconcile_embedding_routes(self, route_names: set[str]) -> None:
        """Mark removed language routes inactive so they can be reclaimed after backfill."""
        with self.connect() as db:
            for row in list(db.execute("SELECT id,manifest FROM embedding_sets WHERE active=1")):
                manifest = json.loads(row["manifest"])
                if manifest.get("route_name", "default") not in route_names:
                    db.execute("UPDATE embedding_sets SET active=0 WHERE id=?", (row["id"],))

    def prune_inactive_embeddings_if_ready(
        self, set_id: str, retain_routes: set[str] | None = None,
    ) -> bool:
        """Keep the previous index until the replacement covers every current transcript."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            active = db.execute(
                "SELECT 1 FROM embedding_sets WHERE id=? AND active=1", (set_id,)
            ).fetchone()
            if not active:
                return False
            coverage = db.execute(
                "SELECT count(*) AS total,coalesce(sum(j.state='done'),0) AS indexed "
                "FROM audio a LEFT JOIN embedding_jobs j ON j.audio_id=a.id AND j.set_id=? "
                "AND j.version=a.transcript_version WHERE a.state='done' "
                "AND length(trim(a.text))>0",
                (set_id,),
            ).fetchone()
            if coverage["total"] != coverage["indexed"]:
                return False
            active_manifest = json.loads(db.execute(
                "SELECT manifest FROM embedding_sets WHERE id=?", (set_id,)
            ).fetchone()[0])
            route_name = active_manifest.get("route_name", "default")
            inactive = [row["id"] for row in db.execute(
                "SELECT id,manifest FROM embedding_sets WHERE active=0"
            ) if (
                json.loads(row["manifest"]).get("route_name", "default") == route_name
                or (retain_routes is not None and json.loads(row["manifest"]).get(
                    "route_name", "default"
                ) not in retain_routes)
            )]
            if inactive:
                marks = ",".join("?" for _ in inactive)
                db.execute(
                    f"DELETE FROM embedding_staging_passages WHERE set_id IN ({marks})", inactive
                )
                db.execute(f"DELETE FROM retrieval_passages WHERE set_id IN ({marks})", inactive)
                db.execute(f"DELETE FROM embedding_jobs WHERE set_id IN ({marks})", inactive)
                db.execute(f"DELETE FROM embedding_sets WHERE id IN ({marks})", inactive)
            return True

    def embedding_coverage(
        self, set_id: str, start: datetime | None = None, end: datetime | None = None
    ) -> dict:
        scope = " AND a.started_at<? AND a.ended_at>?" if start and end else ""
        args = (set_id, stamp(end), stamp(start)) if start and end else (set_id,)
        with self.connect() as db:
            row = db.execute(
                "SELECT count(*) AS total,coalesce(sum(j.state='done'),0) AS indexed "
                "FROM audio a LEFT JOIN embedding_jobs j ON j.audio_id=a.id AND j.set_id=? "
                "AND j.version=a.transcript_version WHERE a.state='done' "
                "AND length(trim(a.text))>0" + scope,
                args,
            ).fetchone()
            return dict(row)

    def _prepare_embedding_passages(self, db, job: dict, manifest: dict, size: int) -> None:
        from .embedding_chunking import chunk_passages
        from .semantic_metadata import json_object, passage_metadata, prepare_source_provenance

        db.execute(
            "DELETE FROM embedding_staging_passages WHERE audio_id=? AND set_id=?",
            (job["audio_id"], job["set_id"]),
        )
        # Populate fingerprints for indexes made before this additive migration.
        for old in db.execute(
            "SELECT id,start_offset,end_offset FROM retrieval_passages "
            "WHERE audio_id=? AND set_id=? AND version=? AND content_hash=''",
            (job["audio_id"], job["set_id"], job["version"]),
        ).fetchall():
            content = job["text"][old["start_offset"]:old["end_offset"]]
            db.execute(
                "UPDATE retrieval_passages SET content_hash=? WHERE id=?",
                (hashlib.sha256(content.encode()).hexdigest(), old["id"]),
            )
        details = json_object(job.get("topic_details") or "{}")
        spans = chunk_passages(
            job["text"], {"passage_chars": size} | manifest, topics=details.get("sections")
        )
        spans = [span for span in spans
                 if job["text"][span["start_offset"]:span["end_offset"]].strip()]
        provenance = prepare_source_provenance(job)
        for ordinal, span in enumerate(spans):
            start, end = span["start_offset"], span["end_offset"]
            content_hash = hashlib.sha256(job["text"][start:end].encode()).hexdigest()
            key = hashlib.sha256(
                f"{job['audio_id']}:{job['set_id']}:{job['version']}:{ordinal}".encode()
            ).hexdigest()
            metadata = passage_metadata(job, span, manifest, provenance) | {"chunk_index": ordinal}
            db.execute(
                "INSERT INTO embedding_staging_passages "
                "(id,audio_id,set_id,version,ordinal,start_offset,end_offset,vector,"
                "content_hash,topic_id,metadata) VALUES(?,?,?,?,?,?,?,"
                "(SELECT vector FROM retrieval_passages WHERE audio_id=? AND set_id=? "
                "AND version=? AND content_hash=? AND vector IS NOT NULL AND ?=0 LIMIT 1),?,?,?)",
                (key, job["audio_id"], job["set_id"], job["version"], ordinal, start, end,
                 job["audio_id"], job["set_id"], job["version"], content_hash, job["force_reembed"],
                 content_hash,
                 metadata.get("topic_id"), json.dumps(metadata, sort_keys=True)),
            )
        db.execute(
            "UPDATE embedding_jobs SET prepared=1 WHERE audio_id=? AND set_id=?",
            (job["audio_id"], job["set_id"]),
        )

    @staticmethod
    def _publish_embedding_passages(db, audio_id: str, set_id: str) -> None:
        db.execute(
            "DELETE FROM retrieval_passages WHERE audio_id=? AND set_id=?", (audio_id, set_id)
        )
        db.execute(
            "INSERT INTO retrieval_passages "
            "(id,audio_id,set_id,version,ordinal,start_offset,end_offset,vector,"
            "content_hash,topic_id,metadata) SELECT "
            "id,audio_id,set_id,version,ordinal,start_offset,end_offset,vector,"
            "content_hash,topic_id,metadata FROM embedding_staging_passages "
            "WHERE audio_id=? AND set_id=?", (audio_id, set_id),
        )
        db.execute(
            "DELETE FROM embedding_staging_passages WHERE audio_id=? AND set_id=?",
            (audio_id, set_id),
        )
        db.execute(
            "UPDATE embedding_jobs SET state='done',attempts=0,retry_at='',"
            "lease_token='',lease_until='' WHERE audio_id=? AND set_id=?", (audio_id, set_id)
        )

    def claim_embedding_batch(self, set_id: str, size: int) -> dict | None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            now = utc_now()
            db.execute(
                "UPDATE embedding_jobs SET state='pending',lease_token='',lease_until='' "
                "WHERE set_id=? AND state='working' AND lease_until<=?", (set_id, stamp(now)),
            )
            while True:
                job = db.execute(
                    "SELECT j.*,a.text,a.topic_details,a.source_metadata,a.started_at,a.ended_at,"
                    "a.segments,a.source_version,"
                    "s.manifest FROM embedding_jobs j JOIN audio a ON a.id=j.audio_id "
                    "AND a.transcript_version=j.version JOIN embedding_sets s ON s.id=j.set_id "
                    "WHERE j.set_id=? AND s.active=1 AND j.state='pending' AND j.retry_at<=? "
                    "AND a.state='done' ORDER BY j.retry_at,a.started_at,a.id LIMIT 1",
                    (set_id, stamp(now)),
                ).fetchone()
                if not job:
                    return None
                job = dict(job)
                manifest = json.loads(job["manifest"])
                if not job["prepared"]:
                    self._prepare_embedding_passages(db, job, manifest, size)
                batch = db.execute(
                    "SELECT * FROM embedding_staging_passages WHERE audio_id=? AND set_id=? "
                    "AND vector IS NULL ORDER BY ordinal LIMIT 16", (job["audio_id"], set_id),
                ).fetchall()
                if not batch:
                    # Boundary/title-only changes often reuse the complete index. No
                    # provider request is needed, but provenance is still refreshed.
                    self._publish_embedding_passages(db, job["audio_id"], set_id)
                    continue
                token, until = uuid4().hex, stamp(now + timedelta(minutes=5))
                db.execute(
                    "UPDATE embedding_jobs SET state='working',attempts=attempts+1,"
                    "lease_token=?,lease_until=? WHERE audio_id=? AND set_id=?",
                    (token, until, job["audio_id"], set_id),
                )
                return job | {
                    "lease_token": token, "lease_until": until,
                    "passages": [dict(row) for row in batch],
                }

    def finish_embedding_batch(self, job: dict, vectors: list[list[float]]) -> bool:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute(
                "SELECT s.manifest FROM audio a JOIN embedding_sets s ON s.id=? AND s.active=1 "
                "JOIN embedding_jobs j ON j.audio_id=a.id AND j.set_id=s.id "
                "WHERE a.id=? AND a.transcript_version=? AND j.version=a.transcript_version "
                "AND j.state='working' AND j.cursor=? AND j.lease_token=? AND a.state='done'",
                (job["set_id"], job["audio_id"], job["version"], job["cursor"], job["lease_token"]),
            ).fetchone()
            if not current:
                return False
            manifest = json.loads(current["manifest"])
            if len(vectors) != len(job["passages"]):
                raise ValueError("Invalid embedding count")
            for passage, vector in zip(job["passages"], vectors, strict=True):
                vector = normalized(vector, manifest["dimension"])
                db.execute(
                    "UPDATE embedding_staging_passages SET vector=? "
                    "WHERE audio_id=? AND set_id=? AND version=? AND content_hash=?",
                    (struct.pack(f"<{len(vector)}f", *vector), job["audio_id"], job["set_id"],
                     job["version"], passage["content_hash"]),
                )
            remaining = db.execute(
                "SELECT min(ordinal) AS ordinal FROM embedding_staging_passages "
                "WHERE audio_id=? AND set_id=? AND vector IS NULL",
                (job["audio_id"], job["set_id"]),
            ).fetchone()
            cursor = remaining["ordinal"] if remaining["ordinal"] is not None else (
                job["cursor"] + len(vectors)
            )
            db.execute(
                "UPDATE embedding_jobs SET cursor=?,state='pending',attempts=0,retry_at='',"
                "lease_token='',lease_until='' WHERE audio_id=? AND set_id=?",
                (cursor, job["audio_id"], job["set_id"]),
            )
            if remaining["ordinal"] is None:
                self._publish_embedding_passages(db, job["audio_id"], job["set_id"])
        self.prune_inactive_embeddings_if_ready(job["set_id"])
        return True

    def retry_embedding(self, job: dict, *, paused: bool = False) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE embedding_jobs SET state='pending',retry_at=?,attempts=?,"
                "lease_token='',lease_until='' "
                "WHERE audio_id=? AND set_id=? AND version=? AND state='working' AND lease_token=?",
                (job["retry_at"] if paused else stamp(utc_now() + timedelta(
                    seconds=min(300, 2 ** min(job["attempts"] + 1, 8))
                )), job["attempts"] + (0 if paused else 1),
                 job["audio_id"], job["set_id"], job["version"], job["lease_token"]),
            )

    def semantic_candidates(
        self,
        set_id: str,
        vector: list[float],
        minimum: float,
        limit: int,
        start: datetime | None,
        end: datetime | None,
        max_per_source: int = 3,
    ) -> list[dict]:
        """Exact scoped passage scan with bounded source diversity and memory use."""
        if limit <= 0 or max_per_source <= 0:
            return []
        scope = " AND a.started_at<? AND a.ended_at>?" if start and end else ""
        args = (set_id, stamp(end), stamp(start)) if start and end else (set_id,)
        heap = []
        with self.connect() as db:
            rows = getattr(db, "iterate", db.execute)(
                "SELECT p.* FROM retrieval_passages p JOIN canonical_audio a ON a.id=p.audio_id "
                "AND a.transcript_version=p.version JOIN embedding_sets s ON s.id=p.set_id "
                "WHERE p.set_id=? AND s.active=1 AND p.vector IS NOT NULL AND a.state='done'"
                + scope
                + " ORDER BY p.audio_id,p.ordinal",
                args,
            )
            previous, source_heap = None, []
            for row in rows:
                if row["audio_id"] != previous:
                    for entry in source_heap:
                        self._push_candidate(heap, entry, limit)
                    source_heap = []
                previous = row["audio_id"]
                try:
                    values = struct.unpack(f"<{len(vector)}f", row["vector"])
                except struct.error:
                    continue  # Corrupt/incompatible derived data never becomes evidence.
                score = math.sumprod(vector, values)
                if math.isfinite(score) and score >= minimum:
                    self._push_candidate(
                        source_heap, (score, row["audio_id"], row["id"], dict(row)),
                        min(limit, max_per_source),
                    )
            for entry in source_heap:
                self._push_candidate(heap, entry, limit)
        result = []
        from .semantic_metadata import json_object

        for score, _, _, row in sorted(heap, reverse=True):
            row.pop("vector", None)
            row["metadata"] = json_object(row["metadata"])
            result.append(row | {"score": score})
        return result

    @staticmethod
    def _push_candidate(heap: list, entry: tuple, limit: int) -> None:
        if len(heap) < limit:
            heapq.heappush(heap, entry)
        elif entry[:3] > heap[0][:3]:
            heapq.heapreplace(heap, entry)
