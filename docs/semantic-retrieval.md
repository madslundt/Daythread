# Semantic retrieval: implementation and operation

## Audit and retained architecture

The original implementation already provided durable SQLite/PostgreSQL embedding jobs,
transcript-version invalidation, scoped FTS/BM25, cosine search, RRF, local calendar query
analysis, language routes, bounded query caching, source validation, and grounded chat.
Those remain the foundation. Recording-topic extraction and explicit source grouping are
reused; generated titles/summaries never replace original speech in embedding inputs.

The main gaps were character-only splitting, one semantic passage per recording,
proximity-only context expansion, no optional reranker, and limited evaluation metrics.
The changes introduce token/topic chunking, atomic publication from staging, attempt leases,
unchanged-content reuse, passage diversity, bounded reranking and expanded evaluation.
No new database service is required. Models and weights are never downloaded by Daythread.

## Chunking and provenance

`topic-token-v1` uses valid ordered topic character ranges, retaining uncovered gaps as
independent searchable spans. Missing, malformed or overlapping ranges fall back to
deterministic sentence/paragraph splitting. Short turns remain together; an oversized
sentence is split without dropping characters. Chunks never span two sources or topics.
Topic segmentation remains derived metadata, not proof that a conversation occurred.

Initial hypotheses are target **320**, maximum **512**, overlap **48** tokens. These are
configurable and uncalibrated, not measured optimal values. `token-sentence-v1` ignores
topics; `characters-overlap-v1` preserves the previous splitter for comparisons. Every
request additionally enforces the prefixed token limit, reserved special tokens and the
character ceiling. Oversized queries fail to keyword retrieval; inputs are not truncated.

For actual model token counts, install `uv sync --extra tokenization` and point
`embedding_tokenizer` at the **absolute path to that model revision's tokenizer.json**.
No Hub lookup occurs. Its file hash and operator revision join the index identity.
Truncation/padding configured in the tokenizer file are disabled for counting. Set
`embedding_special_tokens` to cover the endpoint's BOS/EOS/template overhead.

The dependency-free `utf8` setting counts UTF-8 bytes. For byte-level BPE models such as
Qwen3 this conservatively bounds the number of text tokens; it produces smaller chunks
than actual model tokenization. It is not a universal tokenizer for other model families.
Configure their actual tokenizer before claiming bounded input for those models.

Each passage stores recording UUID, transcript version, ordinal, exact character offsets,
model/revision/strategy, topic ranges and known source timing/grouping. Original transcript
text stays authoritative. Timing is never interpolated from character positions. Exact
segment timing is attached only when source segment text anchors identify exact bounds;
otherwise source-level timing is retained. Names or nearby capture times never establish
speaker identity. Result `matches` can open the original source and exact transcript span.

## Retrieval and lifecycle

Existing query analysis determines dates and referential follow-ups. Scoped lexical and
semantic candidate searches run concurrently. Weighted RRF defaults to `k=60`, weights
1/1, 50 candidates and at most three passages per recording. A recording ranks by its
strongest fused passage, preventing repetition from accumulating an unfair rank boost.
Up to two literal lexical matches retain protected source slots for names/identifiers.
Semantic candidates no longer collapse to one passage before evidence selection.

Nonoverlapping matching excerpts receive context inside their source/topic. Cross-source
neighbors need explicit shared recording/conversation metadata; timing/device adjacency
alone is insufficient. Ranked sources receive one excerpt each before extra passages use
the remaining `retrieval_evidence_character_budget` (default 12,000 characters).
Broad diary/day questions retain the existing explicitly sampled full-transcript path.
This excerpt budget does not bound that separate map/reduce path.

The default cosine floor is -1, avoiding an uncalibrated rejection threshold. Thus semantic
search can return unrelated candidates for an unanswerable question. Calibrate rejection
using held-out negatives and require the answer model to report insufficient evidence;
nearest neighbors do not establish factual relevance or answerability.

New transcripts/imports queue background jobs independently of STT. A batch contains at
most 16 passages; identical content is embedded once and reused within the source/set.
Topic changes requeue without requiring topic extraction to succeed. Completed vectors
remain searchable while staging is rebuilt, and unchanged content reuses vectors.
Publication swaps a complete source atomically. Transcript corrections immediately remove
obsolete evidence and re-embed the corrected source. Deletion/transcript expiry purge
published and staged vectors. Audio-only expiry retains transcript search.

Attempt tokens and expiring leases reject late results after a correction, deletion,
topic update, rebuild or reclaimed job. Retries have bounded backoff and resume after
restart. Changing model, dimension, endpoint, tokenizer, prefixes or chunking selects a
distinct set. Spaces never mix. Older sets are retained until replacement coverage is
complete; queries use the configured set with keyword fallback during replacement.
Only one backend process per archive is supported, as before.

Schema additions migrate automatically on opening the archive: staging table, content
fingerprints, provenance and lease fields. No source migration or manual deletion is
required. Restart after saving model/chunking changes. In web Settings, Semantic search
shows progress and **Rebuild semantic index** safely requeues active sets; equivalent API:
`POST /v1/embeddings/rebuild`. An explicit rebuild requests fresh vectors while retaining
the current published copy until replacement succeeds. Automatic topic/metadata updates
reuse unchanged content. Increment `embedding_revision` to replace weights behind an alias.

## Initial Qwen3-Embedding-0.6B configuration

Qwen documents a 1024-dimensional multilingual model, instruction-formatted queries and
unprefixed documents; larger models have different native dimensions. See the
[official model card](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B).
Use one shared multilingual space: leave `embedding_routes` empty unless evaluation
justifies additional models. Danish/English language switches do not require extra indexes.

Configure in **Settings → Models & server → Semantic search**, or initially with:

```dotenv
OMI_EMBEDDING_ENABLED=true
OMI_EMBEDDING_BASE_URL=http://127.0.0.1:8791/v1
OMI_EMBEDDING_MODEL=daythread-qwen3-embedding
OMI_EMBEDDING_REVISION=YOUR_IMMUTABLE_MODEL_AND_SERVER_REVISION
OMI_EMBEDDING_DIMENSION=1024
OMI_EMBEDDING_QUERY_FORMAT=qwen3
OMI_EMBEDDING_TOKENIZER=/absolute/path/to/pinned-qwen3/tokenizer.json
OMI_EMBEDDING_TOKENIZER_REVISION=YOUR_PINNED_TOKENIZER_REVISION
OMI_EMBEDDING_CHUNKING_STRATEGY=topic-token-v1
OMI_EMBEDDING_TARGET_TOKENS=320
OMI_EMBEDDING_MAX_TOKENS=512
OMI_EMBEDDING_OVERLAP_TOKENS=48
OMI_EMBEDDING_MAX_INPUT_TOKENS=8192
OMI_EMBEDDING_MAX_INPUT_CHARS=4000
OMI_EMBEDDING_TIMEOUT_SECONDS=60
OMI_EMBEDDING_QUERY_TIMEOUT_SECONDS=15
OMI_EMBEDDING_MIN_SCORE=-1
OMI_RERANKER_ENABLED=false
```

Saved settings override environment defaults. The custom server alias above requires
explicit `qwen3` formatting; auto recognizes model names containing `Qwen3-Embedding`.
Auto also supports E5's query/passage prefixes; other names use plain text. Explicit
nonempty prefixes override formatting, and `plain` disables automatic instructions.
The endpoint must return the configured model ID if its response includes `model`.
The revision is an operator assertion: no compatible API can prove unchanged remote
weights. Optional `embedding_send_dimensions` should remain false unless the endpoint
supports that parameter. Every returned vector dimension is validated regardless.

## Explicit CPU-only local serving

One option is [llama.cpp](https://github.com/ggml-org/llama.cpp), using Qwen's
[official GGUF distribution](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B-GGUF).
Download a pinned F16 or Q8_0 artifact and its matching tokenizer yourself, record their
checksums/revisions, and install a pinned llama.cpp build. F16 avoids quantization as an
additional quality variable; measure Q8_0 if desired. Do not use an automatic `-hf`
download command in the backend. A CPU build on Linux can use:

```bash
# In a separately cloned and pinned llama.cpp checkout:
cmake -B build -DGGML_CUDA=OFF -DGGML_METAL=OFF -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release --target llama-server -j 4
./build/bin/llama-server \
  -m /absolute/path/to/qwen3-embedding-0.6b-f16.gguf \
  --alias daythread-qwen3-embedding --embedding --pooling last \
  --n-gpu-layers 0 --ctx-size 8192 --batch-size 8192 --ubatch-size 8192 \
  --parallel 1 --host 127.0.0.1 --port 8791
```

Check the flags against your pinned build's `--help` and
[server contract](https://github.com/ggml-org/llama.cpp/tree/master/tools/server).
Last-token pooling follows Qwen's model implementation. Ensure the endpoint adds no
unexpected template or second instruction. Set Daythread's input limit no higher than
the server's **per-sequence** context. The small model is a reasonable starting point
for a 48GB CPU server; actual latency, concurrent load and RAM remain machine-specific
and unmeasured here. Container deployments need a host-reachable URL instead of loopback.

Probe with fictional text before enabling indexing:

```bash
curl --fail http://127.0.0.1:8791/v1/embeddings \
  -H 'Content-Type: application/json' \
  -d '{"model":"daythread-qwen3-embedding","input":["Peter lovede at ringe."],"encoding_format":"float"}'
```

Expect one indexed, finite, nonzero 1024-element vector. Configure a token only if the
local endpoint requires one. No real-model validation was performed by this implementation.

## Optional reranking and performance limits

`reranker_enabled` defaults false. An explicitly hosted service must implement
`POST {reranker_base_url}/rerank` with model/query/documents/top_n and return every
candidate's unique index and finite relevance_score. At most 30 passages are submitted
by default, with bounded text and a separate timeout. Invalid/unavailable services retain
RRF and lexical protections. Reranking text may be clipped to its configured character
budget; authoritative result excerpts stay unchanged. Qwen3-Reranker needs a compatible
adapter implementing its native chat template and yes/no scoring, not an embeddings API.
See its [official contract](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B).
Benchmark hybrid with/without reranking on the same held-out queries before enabling it.
No reranking quality or latency benefit is claimed here.

Exact cosine scanning streams vectors with bounded top-k/source heaps; PostgreSQL uses
a server cursor rather than materializing the archive. CPU cost remains O(passages ×
dimensions). This preserves exact recall and needs no pgvector installation, but is not
an ANN implementation or a claim of subsecond searches across years of transcripts.
Scope dates where possible, measure the included synthetic scan benchmark, and consider
an optional pgvector/HNSW backend only after measuring target latency and recall against
this exact baseline. Source-level corrections currently re-embed that source rather than
retaining an additional historical vector cache. Speaker-aware splitting and entity/decision
intent-specific ranking beyond existing lexical/date analysis remain future work.

The included [synthetic scan report](retrieval-scan-benchmark.json) measured warm medians
of **28.8 ms / 274 ms / 2.95 s** for 1k / 10k / 100k passages at 1024 dimensions.
The scan's traced Python allocation peak stayed approximately 360 KiB. This excludes
SQLite/native memory and the model server; direct synthetic insertion throughput is
not embedding throughput. A built-in `math.sumprod` kernel reduced the 100k scan from
approximately 5.56 seconds to 2.95 seconds without an approximate index. Reproduce with:

```bash
uv run python scripts/benchmark_retrieval_scan.py --passages 1000 10000
uv run python scripts/benchmark_retrieval_scan.py --passages 100000 --skip-memory
```

See [evaluation commands and recorded results](retrieval-evaluation.md) for metrics,
strategy/model comparisons and the distinction between mock mechanics and real quality.

## Implementation validation, 2026-10-19

All archives used for testing were temporary. The owner's `.env`, private settings and
recording archive were not opened by tests or evaluation. No model weights were loaded.

| Check | Result |
| --- | --- |
| Full backend suite, including real temporary PostgreSQL | 1,633 passed, 4 skipped |
| Web unit tests | 422 passed, 41 files |
| Ruff across repository | Passed |
| Shared web/iOS localization catalogs, seven languages | Passed |
| TypeScript and Vite production build | Passed |
| Python source distribution and wheel | Passed |
| Source offsets, tokens, lifecycle, concurrent updates, stale jobs | Controlled/synthetic regressions passed |
| Actual embedding/reranker quality, Danish/English cross-language accuracy | Not run; requires configured real models |

The installed uv 0.11.32 was older than the project's declared 0.13.1 requirement, so
validation used the existing `.venv/bin/python -m pytest`/`ruff` and `uv --no-config`
for lock/sync/build. The web checks ran with Node 26.8.1/npm 11.19.0, below the documented
deployment baseline. Tests/build passed, but deployment should use the declared toolchain.
Vite retained its existing large-bundle warning. Four optional diarization HTTP checks
were skipped because `multipart` was absent; PostgreSQL tests were enabled and passed.

Browser QA used regular Playwright because the Browser plugin/skill was unavailable.
The flow under test was: Settings → select Default test profile → Advanced → inspect
Search index → Rebuild search index → receive HTTP 200 and refreshed progress.

| Browser check | Desktop 1200×845 / mobile 390×844 |
| --- | --- |
| Page identity / meaningful content / no framework overlay | Passed |
| Index coverage, stopped-worker state and keyword fallback notice | Passed |
| Rebuild interaction and real backend response | Passed |
| Responsive layout / horizontal overflow | Passed / none |
| Console | No component errors; expected 502 model-discovery errors from deliberately absent STT/LLM services |

Screenshots were inspected and kept as temporary QA artifacts outside source. This
checked the incomplete/stopped-worker case; readiness/outage logic and profile gating
also have automated tests. No physical device or actual inference server was exercised.
