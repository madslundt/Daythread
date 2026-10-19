"""Benchmark exact scans over disposable synthetic vectors, never the owner's archive.

Run: python scripts/benchmark_retrieval_scan.py --passages 1000 10000
For a deliberately larger run: --passages 100000 --skip-memory
This measures storage/search mechanics, not embedding-model quality or inference speed.
"""

import argparse
import hashlib
import json
import math
import platform
import random
import struct
import tempfile
import tracemalloc
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import median
from time import perf_counter

from omi_local.domain import stamp
from omi_local.semantic_store import normalized
from omi_local.store import Store


def seed(store: Store, count: int, dimension: int, per_source: int, seed_value: int):
    """Stream inserts using a fixed small vector pool; never allocate an archive-sized list."""
    manifest = {
        "model": "synthetic-scan-only", "revision": "deterministic-pool-v1",
        "dimension": dimension, "metric": "cosine-float32-v1",
        "chunking_strategy": "synthetic-fixed-spans-v1",
    }
    set_id = store.configure_embeddings(manifest)
    randomizer = random.Random(seed_value)
    vectors = [normalized([randomizer.uniform(-1, 1) for _ in range(dimension)], dimension)
               for _ in range(64)]
    packed = [struct.pack(f"<{dimension}f", *vector) for vector in vectors]
    at = datetime(2026, 1, 1, tzinfo=UTC)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        for recording in range(math.ceil(count / per_source)):
            key = f"synthetic-recording-{recording:08d}"
            number = min(per_source, count - recording * per_source)
            pieces = [
                f"Synthetic recording {recording:08d}, passage {ordinal:04d}. "
                + "Fictional decisions, commitments and explanations. " * 6 + "\n"
                for ordinal in range(number)
            ]
            text = "".join(pieces)
            started = at + timedelta(minutes=30 * recording)
            ended = started + timedelta(minutes=30)
            db.execute(
                "INSERT INTO audio(id,device_id,started_at,ended_at,digest,path,state,text,"
                "audio_available,source_kind) VALUES(?,?,?,?,?,?,'done',?,0,?)",
                (key, "synthetic", stamp(started), stamp(ended), key, "unused", text,
                 "synthetic_benchmark"),
            )
            version = db.execute(
                "SELECT transcript_version FROM audio WHERE id=?", (key,),
            ).fetchone()[0]
            offset = 0
            for ordinal, piece in enumerate(pieces):
                end = offset + len(piece)
                metadata = json.dumps({
                    "source_id": key, "transcript_version": version,
                    "source_start": stamp(started), "source_end": stamp(ended),
                    "timing": "source", "embedding_model": manifest["model"],
                    "embedding_revision": manifest["revision"], "chunk_index": ordinal,
                    "chunking_strategy": manifest["chunking_strategy"],
                })
                db.execute(
                    "INSERT INTO retrieval_passages "
                    "(id,audio_id,set_id,version,ordinal,start_offset,end_offset,vector,"
                    "content_hash,metadata) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (f"{key}:{ordinal}", key, set_id, version, ordinal, offset, end,
                     packed[(recording * per_source + ordinal) % len(packed)],
                     hashlib.sha256(piece.encode()).hexdigest(), metadata),
                )
                offset = end
            db.execute(
                "UPDATE embedding_jobs SET state='done' WHERE audio_id=? AND set_id=?",
                (key, set_id),
            )
    return set_id, vectors[0]


def benchmark(count: int, args) -> dict:
    with tempfile.TemporaryDirectory(prefix="daythread-synthetic-scan-") as directory:
        root = Path(directory)
        started = perf_counter()
        store = Store(root)
        set_id, query = seed(
            store, count, args.dimension, args.passages_per_source, args.seed,
        )
        seeding_seconds = perf_counter() - started

        def scan():
            return store.semantic_candidates(
                set_id, query, -1, args.candidates, None, None, args.max_per_source,
            )

        started = perf_counter()
        result = scan()
        first_scan_ms = (perf_counter() - started) * 1000
        latencies = []
        for _ in range(args.repeats):
            started = perf_counter()
            result = scan()
            latencies.append((perf_counter() - started) * 1000)
        report = {
            "passages": count, "recordings": math.ceil(count / args.passages_per_source),
            "dimension": args.dimension, "candidate_limit": args.candidates,
            "max_passages_per_source": args.max_per_source, "returned_passages": len(result),
            "returned_recordings": len({row["audio_id"] for row in result}),
            "seeding_seconds": round(seeding_seconds, 3),
            "seeded_passages_per_second": round(count / seeding_seconds, 1),
            "sqlite_on_disk_bytes": sum(
                path.stat().st_size for path in root.glob("omi.sqlite3*")
            ),
            "first_scan_ms": round(first_scan_ms, 3),
            "warm_scan_samples_ms": [round(value, 3) for value in latencies],
            "warm_scan_median_ms": round(median(latencies), 3),
            "warm_scan_p95_ms": round(sorted(latencies)[
                math.ceil(len(latencies) * 0.95) - 1
            ], 3),
        }
        if not args.skip_memory:
            # Instrumentation greatly slows Python float arithmetic. Keep this separate
            # from latency samples, and distinguish Python allocations from native RSS.
            tracemalloc.start()
            started = perf_counter()
            result = scan()
            traced_ms = (perf_counter() - started) * 1000
            current, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            report.update(
                traced_scan_ms=round(traced_ms, 3),
                traced_python_current_bytes=current, traced_python_peak_bytes=peak,
            )
        return report


def positive(value):
    value = int(value)
    if value <= 0:
        raise argparse.ArgumentTypeError("Value must be positive")
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--passages", type=positive, nargs="+", default=[1000])
    parser.add_argument("--dimension", type=positive, default=1024)
    parser.add_argument("--passages-per-source", type=positive, default=10)
    parser.add_argument("--candidates", type=positive, default=50)
    parser.add_argument("--max-per-source", type=positive, default=3)
    parser.add_argument("--repeats", type=positive, default=3)
    parser.add_argument("--seed", type=int, default=20261019)
    parser.add_argument("--skip-memory", action="store_true")
    args = parser.parse_args()
    print(json.dumps({
        "kind": "synthetic_exact_vector_scan_no_model_quality_claim",
        "python": platform.python_version(), "platform": platform.platform(),
        "dot_product": "math.sumprod; exact float32 vectors, extended-precision accumulation",
        "seed": args.seed, "scope": "entire disposable synthetic corpus",
        "memory_note": "tracemalloc measures Python allocations, not native SQLite/RSS",
        "seeding_note": "direct synthetic vector inserts; not model indexing throughput",
        "runs": [benchmark(count, args) for count in args.passages],
    }, indent=2))


if __name__ == "__main__":
    main()
