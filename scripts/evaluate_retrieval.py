"""Evaluate fictional retrieval; real embedding/reranker endpoints require explicit opt-in."""

import argparse
import asyncio
import json
import math
import tempfile
import tracemalloc
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from statistics import mean
from time import perf_counter
from uuid import NAMESPACE_URL, uuid5

import httpx

from omi_local.embeddings import Embeddings, index_one
from omi_local.reranking import Reranker
from omi_local.service import Memory
from omi_local.settings import Settings
from omi_local.store import Store


class NoGeneration:
    async def chat(self, *args, **kwargs):
        raise RuntimeError("This evaluation must never generate answers")


def metrics(ranked: list[str], relevance: dict[str, int]) -> dict:
    # Grade zero is an explicit negative, and duplicate passages cannot inflate source recall.
    relevance = {key: value for key, value in relevance.items() if value > 0}
    ranked = list(dict.fromkeys(ranked))
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
        "recall_at_10": len(set(ranked[:10]) & relevant) / len(relevant),
        "recall_at_12": len(set(ranked[:12]) & relevant) / len(relevant),
        "precision_at_5": len(set(ranked[:5]) & relevant) / 5,
        "precision_at_10": len(set(ranked[:10]) & relevant) / 10,
        "retrieval_precision": len(set(ranked) & relevant) / len(ranked) if ranked else 0,
        "mrr_at_10": 1 / min(ranks) if ranks else 0,
        "ndcg_at_10": dcg / idcg if idcg else 0,
        "evidence_coverage_at_10": float(relevant <= set(ranked[:10])),
        "required_context_at_12": float(relevant <= set(ranked[:12])),
    }


def span_coverage(sources: list[dict], required: list[dict], reverse: dict[str, str]) -> dict:
    """Require each exact evidence span to be covered by returned authoritative excerpts."""
    if not required:
        return {}
    covered = 0
    for span in required:
        intervals = sorted(
            (match["start_offset"], match["end_offset"])
            for source in sources
            if reverse[source["id"]] == span["source"]
            for match in source.get("matches", [])
        )
        cursor = span["start_offset"]
        for start, end in intervals:
            if start > cursor:
                break
            cursor = max(cursor, end)
        covered += cursor >= span["end_offset"]
    return {
        "passage_evidence_coverage": covered / len(required),
        "all_passage_evidence": float(covered == len(required)),
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
        "irrelevant_sources": sum(row["irrelevant_sources"] for row in rows),
        "latency_mean_ms": round(mean(latencies), 2) if latencies else None,
        "latency_p95_ms": round(latencies[math.ceil(len(latencies) * 0.95) - 1], 2)
        if latencies
        else None,
    }


def evaluation_settings(root: str, live: bool, overrides: dict | None = None) -> Settings:
    overrides = overrides or {}
    allowed = {
        key
        for key in Settings.model_fields
        if key.startswith(
            (
                "embedding_",
                "retrieval_",
                "reranker_",
                "rerank_",
            )
        )
    } - {"embedding_enabled"}
    if overrides.keys() - allowed:
        raise ValueError("Evaluation overrides must only configure embedding/retrieval/reranking")
    if not live and any(
        key.startswith(("rerank_", "reranker_")) and value for key, value in overrides.items()
    ):
        raise ValueError("Reranking requires explicit --live-embeddings opt-in")
    # Explicit paths and _env_file=None prohibit opening .env, saved settings or owner archives.
    return Settings(
        **overrides,
        data_dir=Path(root),
        worker_enabled=False,
        timezone="UTC",
        embedding_enabled=live,
        _env_file=None,
    )


def load_documents(memory: Memory, fixture: dict) -> tuple[dict, dict]:
    keys, reverse = {}, {}
    for document in fixture["documents"]:
        key = uuid5(NAMESPACE_URL, "omi-fictional-retrieval/" + document["id"])
        keys[document["id"]], reverse[str(key)] = str(key), document["id"]
        at = datetime.combine(date.fromisoformat(document["day"]), datetime.min.time(), UTC)
        at += timedelta(hours=12)
        memory.store.ingest(key, "fictional-eval", at, b"fixture-no-audio", 30)
        memory.store.complete(str(key), document["text"])
        if document.get("sections"):
            # Human-authored synthetic boundaries exercise topic chunking without invoking an LLM.
            with memory.store.connect() as db:
                db.execute(
                    "UPDATE audio SET topic_details=? WHERE id=?",
                    (
                        json.dumps({"sections": document["sections"], "fingerprint": "fictional"}),
                        str(key),
                    ),
                )
    return keys, reverse


async def evaluate(
    fixture: dict,
    live: bool,
    modes: list[str],
    overrides: dict | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict:
    if not live and any(mode != "keyword" for mode in modes):
        raise ValueError("Semantic/hybrid evaluation requires explicit live endpoint opt-in")
    with tempfile.TemporaryDirectory(prefix="omi-retrieval-eval-") as root:
        config = evaluation_settings(root, live, overrides)
        memory = Memory(Store(config.data_dir), config, NoGeneration())
        keys, reverse = load_documents(memory, fixture)
        async with httpx.AsyncClient(follow_redirects=False, transport=transport) as http:
            memory.retriever.embeddings = Embeddings(config, http)
            adapters = {"default": memory.retriever.embeddings}
            memory.retriever.embedding_adapters = adapters
            memory.retriever.reranker = Reranker(config, http)
            for route in config.embedding_languages:
                adapters[route] = Embeddings(config.embedding_route_settings(route), http)
            for route in adapters:
                adapters[route] = Embeddings(config.embedding_route_settings(route), http)
            if "default" in adapters:
                adapters["default"] = memory.retriever.embeddings
            indexing_start = perf_counter()
            if live:
                sets = {
                    "default": memory.retriever.set_id,
                    **getattr(memory.retriever, "route_set_ids", {}),
                }
                for route, set_id in sets.items():
                    adapter = adapters.get(route, memory.retriever.embeddings)
                    while await index_one(memory.store, adapter, set_id, set(sets)):
                        # A failed job is delayed by the worker; the coverage check below fails
                        # closed, avoiding a quality claim based on partial/fallback results.
                        pass
                    coverage = memory.retriever.status(route=route)
                    if coverage["indexed"] != coverage["total"]:
                        raise RuntimeError(
                            "Embedding backfill incomplete; no quality result produced"
                        )
            indexing_ms = (perf_counter() - indexing_start) * 1000
            with memory.store.connect() as db:
                passage_count = db.execute("SELECT count(*) FROM retrieval_passages").fetchone()[0]
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
                    if (
                        config.model_dump().get("reranker_enabled")
                        and mode == "hybrid"
                        and result["sources"]
                    ):
                        rerank = details.get("reranking", details.get("reranker", {}))
                        if (rerank if isinstance(rerank, str) else rerank.get("state")) != "ready":
                            raise RuntimeError(
                                "Reranking incomplete; no reranked quality result produced"
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
                            "metrics": metrics(ranked, query["relevance"])
                            | span_coverage(
                                result["sources"],
                                query.get("required_spans", []),
                                reverse,
                            ),
                            "latency_ms": elapsed,
                            "scope_violations": violations,
                            "irrelevant_sources": sum(
                                query["relevance"].get(key, 0) <= 0 for key in ranked
                            ),
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
                "run_kind": "mock_adapter" if transport else "real_endpoint" if live else "keyword",
                "live_embeddings": bool(live and not transport),
                "answer_quality_tested": False,
                "documents": len(keys),
                "configuration": {
                    key: value
                    for key, value in config.model_dump().items()
                    if key.startswith(("embedding_", "retrieval_", "reranker_"))
                    and key
                    not in {
                        "embedding_token",
                        "embedding_base_url",
                        "embedding_routes",
                        "embedding_query_prefix",
                        "embedding_document_prefix",
                        "embedding_query_instruction",
                        "embedding_tokenizer",
                        "reranker_token",
                        "reranker_base_url",
                    }
                },
                "embedding_set": memory.retriever.set_id,
                "coverage": memory.retriever.status(),
                "indexing_ms": round(indexing_ms, 2),
                "indexed_passages": passage_count,
                "indexing_passages_per_second": round(passage_count * 1000 / indexing_ms, 2)
                if live and indexing_ms
                else None,
                "database_bytes": (Path(root) / "omi.sqlite3").stat().st_size,
                "results": results,
            }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixture",
        type=Path,
        default=(Path(__file__).parents[1] / "tests/fixtures/retrieval_eval.json"),
    )
    parser.add_argument(
        "--live-embeddings",
        action="store_true",
        help=("Explicitly contact configured OMI_EMBEDDING_* and optional reranking endpoints"),
    )
    parser.add_argument("--mode", action="append", choices=("keyword", "semantic", "hybrid"))
    parser.add_argument(
        "--matrix",
        type=Path,
        help=(
            "JSON object mapping comparison names to embedding/retrieval/reranker "
            "settings overrides"
        ),
    )
    args = parser.parse_args()
    modes = args.mode or (
        ["keyword", "semantic", "hybrid"] if args.live_embeddings else ["keyword"]
    )
    if not args.live_embeddings and any(mode != "keyword" for mode in modes):
        parser.error("Semantic/hybrid evaluation requires --live-embeddings")
    fixture = json.loads(args.fixture.read_text())
    variants = json.loads(args.matrix.read_text()) if args.matrix else {"configured": {}}
    if not isinstance(variants, dict) or not variants:
        parser.error("Matrix must contain at least one named settings object")
    reports = {}
    for name, overrides in variants.items():
        tracemalloc.start()
        try:
            report = asyncio.run(evaluate(fixture, args.live_embeddings, modes, overrides))
            report["python_peak_traced_bytes"] = tracemalloc.get_traced_memory()[1]
            report["memory_scope"] = (
                "Python allocations only; excludes native and model server memory"
            )
            reports[name] = report
        finally:
            tracemalloc.stop()
    print(json.dumps(reports if args.matrix else reports["configured"], indent=2))


if __name__ == "__main__":
    main()
