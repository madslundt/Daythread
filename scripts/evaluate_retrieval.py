"""Evaluate fictional retrieval separately from answers; live embeddings require explicit opt-in."""

import argparse
import asyncio
import json
import math
import tempfile
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from statistics import mean
from time import perf_counter
from uuid import NAMESPACE_URL, uuid5

import httpx

from omi_local.embeddings import Embeddings, index_one
from omi_local.service import Memory
from omi_local.settings import Settings
from omi_local.store import Store


class NoGeneration:
    async def chat(self, *args, **kwargs):
        raise RuntimeError("This evaluation must never generate answers")


def metrics(ranked: list[str], relevance: dict[str, int]) -> dict:
    relevant = set(relevance)
    if not relevant:
        return {"negative_rejected": float(not ranked)}
    gains = [relevance.get(key, 0) for key in ranked[:10]]
    dcg = sum((2**gain - 1) / math.log2(index + 2) for index, gain in enumerate(gains))
    ideal = sorted(relevance.values(), reverse=True)[:10]
    idcg = sum((2**gain - 1) / math.log2(index + 2) for index, gain in enumerate(ideal))
    ranks = [index + 1 for index, key in enumerate(ranked[:10]) if key in relevant]
    return {
        "recall_at_5": len(set(ranked[:5]) & relevant) / len(relevant),
        "recall_at_12": len(set(ranked[:12]) & relevant) / len(relevant),
        "mrr_at_10": 1 / min(ranks) if ranks else 0,
        "ndcg_at_10": dcg / idcg if idcg else 0,
        "required_context_at_12": float(relevant <= set(ranked[:12])),
    }


def aggregate(rows: list[dict]) -> dict:
    values = defaultdict(list)
    for row in rows:
        for key, value in row["metrics"].items():
            values[key].append(value)
    latencies = sorted(row["latency_ms"] for row in rows)
    return {
        "queries": len(rows),
        **{key: round(mean(group), 4) for key, group in values.items()},
        "scope_violations": sum(row["scope_violations"] for row in rows),
        "latency_p95_ms": round(latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))], 2),
    }


async def evaluate(fixture: dict, live: bool, modes: list[str]) -> dict:
    with tempfile.TemporaryDirectory(prefix="omi-retrieval-eval-") as root:
        # Never read the owner's .env or open the owner's live archive. Explicit live mode
        # reads OMI_EMBEDDING_* environment settings and contacts only that local adapter.
        config = Settings(
            data_dir=Path(root),
            worker_enabled=False,
            timezone="UTC",
            embedding_enabled=live,
            _env_file=None,
        )
        memory = Memory(Store(config.data_dir), config, NoGeneration())
        keys, reverse = {}, {}
        for document in fixture["documents"]:
            key = uuid5(NAMESPACE_URL, "omi-fictional-retrieval/" + document["id"])
            keys[document["id"]], reverse[str(key)] = str(key), document["id"]
            at = datetime.combine(date.fromisoformat(document["day"]), datetime.min.time(), UTC)
            at += timedelta(hours=12)
            memory.store.ingest(key, "fictional-eval", at, b"fixture-no-audio", 30)
            memory.store.complete(str(key), document["text"])
        async with httpx.AsyncClient(follow_redirects=False) as http:
            memory.retriever.embeddings = Embeddings(config, http)
            indexing_start = perf_counter()
            if live:
                while await index_one(
                    memory.store, memory.retriever.embeddings, memory.retriever.set_id
                ):
                    pass
                coverage = memory.retriever.status()
                if coverage["indexed"] != coverage["total"]:
                    raise RuntimeError("Embedding backfill incomplete; no quality result produced")
            indexing_ms = (perf_counter() - indexing_start) * 1000
            results = {}
            for mode in modes:
                rows = []
                for query in fixture["queries"]:
                    scope = {
                        key: date.fromisoformat(query[key])
                        for key in ("day", "start_day", "end_day")
                        if key in query
                    }
                    before = perf_counter()
                    result = await memory.search(
                        query["question"], 12, retrieval_mode=mode, **scope
                    )
                    elapsed = (perf_counter() - before) * 1000
                    details = result["retrieval_details"]
                    if mode in {"semantic", "hybrid"} and (
                        result["retrieval"] != mode
                        or details.get("fallback_reason")
                        or details.get("partial")
                        or details["embedding"]["state"] != "ready"
                        or details["embedding"]["indexed"] != details["embedding"]["total"]
                    ):
                        raise RuntimeError(
                            f"{mode} evaluation failed for {query['id']}: degraded retrieval; "
                            "no quality result produced"
                        )
                    ranked = [reverse[source["id"]] for source in result["sources"]]
                    violations = 0
                    if result["scope"]:
                        for source in result["sources"]:
                            violations += not (
                                source["started_at"] < result["scope"]["end"]
                                and source["ended_at"] > result["scope"]["start"]
                            )
                    rows.append(
                        {
                            "id": query["id"],
                            "category": query["category"],
                            "split": query["split"],
                            "ranked": ranked,
                            "metrics": metrics(ranked, query["relevance"]),
                            "latency_ms": elapsed,
                            "scope_violations": violations,
                        }
                    )
                groups = defaultdict(list)
                for row in rows:
                    groups[row["category"]].append(row)
                    groups[row["split"]].append(row)
                results[mode] = {
                    "overall": aggregate(rows),
                    "groups": {name: aggregate(group) for name, group in groups.items()},
                    "queries": rows,
                }
            return {
                "fictional": True,
                "live_embeddings": live,
                "answer_quality_tested": False,
                "documents": len(keys),
                "embedding_set": memory.retriever.set_id,
                "coverage": memory.retriever.status(),
                "indexing_ms": round(indexing_ms, 2),
                "database_bytes": (Path(root) / "omi.sqlite3").stat().st_size,
                "results": results,
            }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixture",
        type=Path,
        default=Path(__file__).parents[1] / "tests/fixtures/retrieval_eval.json",
    )
    parser.add_argument(
        "--live-embeddings",
        action="store_true",
        help="Explicitly contact configured local OMI_EMBEDDING_* service",
    )
    parser.add_argument("--mode", action="append", choices=("keyword", "semantic", "hybrid"))
    args = parser.parse_args()
    modes = args.mode or (
        ["keyword", "semantic", "hybrid"] if args.live_embeddings else ["keyword"]
    )
    if not args.live_embeddings and any(mode != "keyword" for mode in modes):
        parser.error("Semantic/hybrid evaluation requires --live-embeddings")
    print(
        json.dumps(
            asyncio.run(
                evaluate(json.loads(args.fixture.read_text()), args.live_embeddings, modes)
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
