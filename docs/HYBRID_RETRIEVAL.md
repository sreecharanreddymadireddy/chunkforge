# Hybrid retrieval and dependency resilience

## Execution contract

`make search ARGS='--query "How does DivOPD remove dead rows?" --strategy recursive'`
runs the retrieval CLI. It does not start the HTTP API or generate an answer.
The existing `/health` endpoint remains a liveness check only.

1. Read a strategy/model/pooling-compatible chunk snapshot from PostgreSQL.
2. Build a process-local BM25 index over chunk core text.
3. Encode the query with BGE, using Redis on repeated compatible inputs.
4. Fetch dense top-50 and BM25 top-50; both use the snapshot's document set.
5. Fuse unique `(doc_id, chunk_index)` identities with `sum(1 / (60 + rank))`.
6. Cross-encode the first 20 fused candidates and return at most five.

All raw scores remain separate. Cosine similarity, BM25 score, RRF score and
cross-encoder logit are different quantities; none is a confidence percentage.
The cross-encoder sees each question with each candidate core, not an expanded
sentence-window context. Long passages are split into tokenizer-budgeted windows;
the maximum window logit represents the candidate. Queries over 128 reranker
tokens are explicitly rejected. The pair budget is 512 model tokens.

The published model identifier is `cross-encoder/ms-marco-MiniLM-L6-v2`. An exact
legacy extra-hyphen spelling is normalized by the CLI for existing local env files.

## Commands

```bash
make setup
make docker-up
make search ARGS='--query "What is Endo-Modality Proxy Training in QiYao-M?" --strategy recursive --repeat 2 --output /tmp/chunkforge-search.json'
make search ARGS='--query DivOPD --repeat 4 --fail-dense --output /tmp/chunkforge-outage.json'
CHUNKFORGE_RUN_PGVECTOR_TESTS=1 CHUNKFORGE_RUN_REDIS_TESTS=1 make check
```

`--output` writes stage traces, including candidate identities and 400-character
excerpts. `--verbose` prints all candidates as well. Pipeline results themselves
retain complete source text and offsets; only CLI trace serialization is shortened.
Repeat `--query` to inspect several queries without reloading the models.
The first run downloads public model weights; normal unit tests use fixtures.

Cache timings describe the query embedding stage, including Redis access. Total
search still includes BM25, database access and cross-encoder work. Do not call a
cache hit an end-to-end speedup if the measured total did not improve.

`make embed ARGS='--cache'` optionally caches independent document-core and query
vectors during indexing. Late chunk vectors are already derived from contextual
token outputs and are preserved, not replaced with standalone cached embeddings.
Semantic boundary detection and contextual model passes are not cached by this layer.

## Failure policy

| Dependency/event | Behavior |
|---|---|
| Redis unavailable/corrupt entry | Miss; compute normally; increment cache error counter |
| Vector query operational failure | At most three attempts with full-jitter backoff |
| Three exhausted vector calls | Open circuit for 30 seconds; fail fast to BM25 |
| Cooldown ends | Admit one half-open probe; close on success or reopen on failure |
| Dense path unavailable | Return `bm25_only` evidence with explicit reason |
| Reranker operational failure | Return fused order as `hybrid_unreranked` |
| Blank input, invalid vector, malformed scores | Raise validation failure; do not disguise as outage |

The breaker wraps the logical call; retries are inside it. Therefore three attempts
for one request count as one breaker failure. Redis client automatic retries are
disabled; connect/read timeouts are configured separately (0.25 seconds each).
Vector database connections have a ten-second connect timeout. A local inference
call has no hard cancellation deadline in v1; retries cannot fix a hung computation.

## Boundaries and next changes

- BM25 is loaded once per command and remains available if PostgreSQL later fails.
  A cold process still requires PostgreSQL and model loading. This is warm-process
  graceful degradation, not independent disaster recovery.
- The CLI constrains dense search to the lexical snapshot's document IDs during
  additive indexing. Concurrent replacement/deletion of existing documents is not
  a fully version-pinned snapshot. An ingestion-driven refresh/version swap is future work.
- This is an in-memory `rank-bm25` implementation, not a scalable inverted-index
  service. It scans candidate token sets. PostgreSQL full-text search or a dedicated
  search engine should replace it for a much larger corpus.
- Cache counters and breakers are per-process. There is no distributed breaker,
  single-flight cache stampede protection, model-serving process isolation, or
  end-to-end deadline/bulkhead yet. Redis TLS/auth and tenant-aware cache identities
  are deployment concerns; this local corpus is not tenant-scoped.
- Dense and lexical execution are sequential for transparent traces. Parallel
  execution, pooling and latency tuning should follow measured bottlenecks.
- Keyword overlap, embeddings and cross-encoders can all mishandle negation,
  identifiers, equations or bad PDF reading order. Hybrid retrieval is not a proof
  of factual correctness. Held-out recall/MRR evaluation belongs to the eval phase.
- API search/ask routes, generated answers, citations and the UI remain later phases.

See [measured examples](../eval/HYBRID_RESULTS.md) and
[resilience measurements](../eval/RESILIENCE_RESULTS.md).
