# Retrieval evaluation

`scripts/evaluate_retrieval.py` evaluates retrieval without answer generation, using a new temporary SQLite archive for each run and comparison variant. It does not load `.env`, saved private settings, recordings, or the owner's archive. The default run uses keyword search and never contacts inference. Tests use explicitly mocked HTTP responses; these verify contracts and failure handling, not semantic accuracy.

The existing `tests/fixtures/retrieval_eval.json` format and 24-document/36-query corpus remain supported. `tests/fixtures/retrieval_eval_conversations.json` adds 10 fictional conversations and 14 questions covering Danish, English, mixed language, exact identifiers, similar people/projects, decisions and their rationale, commitments, scoped dates, difficult negatives, and evidence across multiple sources and distant passages. Human-authored topic sections exercise topic boundaries without generating them. Exact `required_spans` supplement source-level graded relevance; retrieving a recording alone does not establish that the answer's needed passages reached the evidence budget. Development and held-out groups are reported separately.

```sh
uv run python scripts/evaluate_retrieval.py
uv run python scripts/evaluate_retrieval.py \
  --fixture tests/fixtures/retrieval_eval_conversations.json
```

Real endpoints require explicit `--live-embeddings`. Configure the model service yourself first; this command does not download or launch weights. Pass model configuration as environment variables or in the named matrix settings. Export any credential separately; never place it in a saved report or checked-in matrix. Saved application settings are intentionally ignored. For example, for a separately configured local Qwen3 service:

```sh
OMI_EMBEDDING_BASE_URL=http://127.0.0.1:4000/v1 \
OMI_EMBEDDING_MODEL=Qwen/Qwen3-Embedding-0.6B \
OMI_EMBEDDING_REVISION=your-immutable-weights-revision \
OMI_EMBEDDING_DIMENSION=1024 \
uv run python scripts/evaluate_retrieval.py --live-embeddings \
  --fixture tests/fixtures/retrieval_eval_conversations.json \
  --matrix tests/fixtures/retrieval_eval_matrix.json > retrieval-report.json
```

The matrix maps names to validated embedding, retrieval, or reranker settings. It compares the character baseline with sentence/token and topic/token strategies at several sizes and overlaps. Each variant receives a fresh archive and index. Each live variant runs keyword, semantic and hybrid modes independently; repeat `--mode` to choose a subset. Configure the model's actual local tokenizer and exact input/template budgets when available. The conservative UTF-8 default can create smaller chunks than the nominal token target; compare actual passage counts as well as target settings.

To compare models, copy the matrix and add named entries with `embedding_model`, `embedding_revision`, `embedding_dimension`, and the appropriate tokenizer and formatting settings. To compare optional reranking, duplicate an otherwise identical entry and set `reranker_enabled`, `reranker_model` and `reranker_base_url`. This explicitly configured service must implement the documented `/rerank` contract; Qwen3-Reranker weights alone do not imply that API. Reranking is off by default and only changes hybrid ranking. Semantic/hybrid runs fail without publishing a quality report if embeddings are incomplete or retrieval falls back; a hybrid run with candidates also fails if its requested reranker is unavailable. An empty candidate set legitimately requires no reranker call.

Reported measurements include:

- Source Recall@5/10 (plus legacy Recall@12), MRR@10, graded nDCG@10, precision@5/10, and precision among returned sources. Duplicate source IDs cannot inflate source recall; relevance grade zero is negative. Precision@k uses a fixed denominator of k, including when fewer results return.
- Complete source-evidence coverage@10, legacy required-context@12, exact-span coverage and complete-span coverage for annotated questions. Positive-query metrics exclude questions with no relevant sources; negatives have a separate rejection rate. Their returned sources also count as irrelevant.
- Explicit scope violations, irrelevant source count, query mean/p95 latency, indexed passage throughput, SQLite file size, and traced peak Python allocation bytes. The peak excludes native libraries, model-server memory and total process RSS. Latency includes in-process retrieval and endpoint calls; no production concurrency or server-memory claim follows from this small fixture.
- `run_kind`: `keyword`, `mock_adapter`, or `real_endpoint`. `live_embeddings` is false for mock adapters, even though they exercise the semantic pipeline. `answer_quality_tested` is always false. Reports include relevant non-secret configuration and a versioned embedding-set identity, while excluding endpoint credentials and prefix/instruction text.

## Measured development baseline, 2026-10-19

Only keyword evaluation was run against real retrieval code. No embedding or reranking model was contacted. Machine-readable reports are in [benchmarks](benchmarks/); allocation tracing was enabled and other development checks ran concurrently, so latency is a diagnostic sample, not a capacity estimate.

| Fictional corpus | Queries | Recall@5 / @10 | MRR@10 | nDCG@10 | Returned-source precision | Negative rejection | Scope violations |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Existing fixture | 36 | 0.7167 / 0.7167 | 0.7167 | 0.6971 | 0.6222 | 0.6667 | 0 |
| Conversation fixture | 14 | 1.0000 / 1.0000 | 0.7533 | 0.8211 | 0.6167 | 0.2500 | 0 |

Conversation questions with exact evidence annotations achieved 0.6667 mean span coverage; only 0.25 included all required spans. This exposes evidence-budget/context gaps that perfect source recall would conceal. Difficult negatives often share vocabulary with real evidence; keyword search alone does not establish that a question is answerable.

Running the five chunking matrix entries in keyword-only mode produced identical source-quality metrics, as expected: no passage embeddings were built. This exercises configuration and fixture compatibility but cannot select a chunking default. The proposed 320/512/48 topic/token values remain initial engineering hypotheses until a configured model is measured on held-out questions. Mock vector tests are deliberately excluded from the quality table. Follow-up measurements should use realistic corpus sizes, model-server RSS, bounded concurrent queries/indexing, and separately report cold/warm cache behavior before changing storage or default reranking policy.
