"""Rebuildable passage index. Source text and versions remain authoritative."""

import hashlib
import heapq
import json
import math
import struct
from datetime import datetime, timedelta

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
                # Resume leases after restart. Only one archive process is supported.
                db.execute(
                    "UPDATE embedding_jobs SET state='pending' WHERE set_id=? AND state='working'",
                    (key,),
                )
                db.execute(
                    "INSERT OR IGNORE INTO embedding_jobs(audio_id,set_id,version) "
                    "SELECT id,?,transcript_version FROM audio "
                    "WHERE state='done' AND length(trim(text))>0",
                    (key,),
                )
        return key

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

    def claim_embedding_batch(self, set_id: str, size: int) -> dict | None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            job = db.execute(
                "SELECT j.*,a.text FROM embedding_jobs j JOIN audio a ON a.id=j.audio_id "
                "AND a.transcript_version=j.version JOIN embedding_sets s ON s.id=j.set_id "
                "WHERE j.set_id=? AND s.active=1 AND j.state='pending' AND j.retry_at<=? "
                "AND a.state='done' ORDER BY j.retry_at,a.started_at,a.id LIMIT 1",
                (set_id, stamp(utc_now())),
            ).fetchone()
            if not job:
                return None
            job = dict(job)
            if job["cursor"] == 0:
                db.execute(
                    "DELETE FROM retrieval_passages WHERE audio_id=? AND set_id=?",
                    (job["audio_id"], set_id),
                )
                spans = (
                    (start, end) for start, end in passages(job["text"], size)
                    if job["text"][start:end].strip()
                )
                for ordinal, (start, end) in enumerate(spans):
                    key = hashlib.sha256(
                        f"{job['audio_id']}:{set_id}:{job['version']}:{ordinal}".encode()
                    ).hexdigest()
                    db.execute(
                        "INSERT INTO retrieval_passages VALUES(?,?,?,?,?,?,?,NULL)",
                        (key, job["audio_id"], set_id, job["version"], ordinal, start, end),
                    )
            batch = db.execute(
                "SELECT * FROM retrieval_passages WHERE audio_id=? AND set_id=? "
                "AND ordinal>=? ORDER BY ordinal LIMIT 16",
                (job["audio_id"], set_id, job["cursor"]),
            ).fetchall()
            db.execute(
                "UPDATE embedding_jobs SET state='working',attempts=attempts+1 "
                "WHERE audio_id=? AND set_id=?",
                (job["audio_id"], set_id),
            )
            return job | {"passages": [dict(row) for row in batch]}

    def finish_embedding_batch(self, job: dict, vectors: list[list[float]]) -> bool:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute(
                "SELECT 1 FROM audio a JOIN embedding_sets s ON s.id=? AND s.active=1 "
                "JOIN embedding_jobs j ON j.audio_id=a.id AND j.set_id=s.id "
                "WHERE a.id=? AND a.transcript_version=? AND j.version=a.transcript_version "
                "AND j.state='working' AND j.cursor=? AND a.state='done'",
                (job["set_id"], job["audio_id"], job["version"], job["cursor"]),
            ).fetchone()
            if not current:
                return False
            manifest = json.loads(
                db.execute(
                    "SELECT manifest FROM embedding_sets WHERE id=?",
                    (job["set_id"],),
                ).fetchone()[0]
            )
            if len(vectors) != len(job["passages"]):
                raise ValueError("Invalid embedding count")
            for passage, vector in zip(job["passages"], vectors, strict=True):
                vector = normalized(vector, manifest["dimension"])
                db.execute(
                    "UPDATE retrieval_passages SET vector=? WHERE id=? AND version=?",
                    (struct.pack(f"<{len(vector)}f", *vector), passage["id"], job["version"]),
                )
            cursor = job["cursor"] + len(vectors)
            remaining = db.execute(
                "SELECT 1 FROM retrieval_passages WHERE audio_id=? AND set_id=? AND ordinal>=?",
                (job["audio_id"], job["set_id"], cursor),
            ).fetchone()
            db.execute(
                "UPDATE embedding_jobs SET cursor=?,state=?,attempts=0,retry_at='' "
                "WHERE audio_id=? AND set_id=? AND version=?",
                (
                    cursor,
                    "pending" if remaining else "done",
                    job["audio_id"],
                    job["set_id"],
                    job["version"],
                ),
            )
            return True

    def retry_embedding(self, job: dict, *, paused: bool = False) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE embedding_jobs SET state='pending',retry_at=?,attempts=? "
                "WHERE audio_id=? AND set_id=? AND version=? AND state='working'",
                (
                    job["retry_at"] if paused else
                    stamp(
                        utc_now() + timedelta(seconds=min(300, 2 ** min(job["attempts"] + 1, 8)))
                    ),
                    job["attempts"] + (0 if paused else 1),
                    job["audio_id"],
                    job["set_id"],
                    job["version"],
                ),
            )

    def semantic_candidates(
        self,
        set_id: str,
        vector: list[float],
        minimum: float,
        limit: int,
        start: datetime | None,
        end: datetime | None,
    ) -> list[dict]:
        """Exact scoped scan, streamed by recording; memory is bounded by candidate count."""
        scope = " AND a.started_at<? AND a.ended_at>?" if start and end else ""
        args = (set_id, stamp(end), stamp(start)) if start and end else (set_id,)
        heap = []
        with self.connect() as db:
            rows = db.execute(
                "SELECT p.* FROM retrieval_passages p JOIN canonical_audio a ON a.id=p.audio_id "
                "AND a.transcript_version=p.version JOIN embedding_sets s ON s.id=p.set_id "
                "WHERE p.set_id=? AND s.active=1 AND p.vector IS NOT NULL AND a.state='done'"
                + scope
                + " ORDER BY p.audio_id,p.ordinal",
                args,
            )
            previous, best = None, None
            for row in rows:
                if row["audio_id"] != previous and best:
                    self._push_candidate(heap, best, limit)
                    best = None
                previous = row["audio_id"]
                try:
                    values = struct.unpack(f"<{len(vector)}f", row["vector"])
                except struct.error:
                    continue  # Corrupt/incompatible derived data never becomes evidence.
                score = sum(a * b for a, b in zip(vector, values, strict=True))
                if math.isfinite(score) and score >= minimum and (not best or score > best[0]):
                    best = (score, row["audio_id"], row["id"], dict(row))
            if best:
                self._push_candidate(heap, best, limit)
        return [entry[3] | {"score": entry[0]} for entry in sorted(heap, reverse=True)]

    @staticmethod
    def _push_candidate(heap: list, entry: tuple, limit: int) -> None:
        if len(heap) < limit:
            heapq.heappush(heap, entry)
        elif entry[:3] > heap[0][:3]:
            heapq.heapreplace(heap, entry)
