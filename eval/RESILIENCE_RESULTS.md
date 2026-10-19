# Resilience verification

Measured on 2026-10-19 with local Redis, PostgreSQL and downloaded inference models.

## Real query embedding cache

| Question | First embedding stage (ms) | Cached repeat (ms) |
|---|---:|---:|
| DivOPD dead rows | 32.814 | 1.898 |
| QiYao-M proxy training | 28.310 | 6.739 |
| HyperReservoir bilinear readout | 131.776 | 7.432 |

Each repeat increased the Redis cache-hit count without recomputing the query
embedding. These timings include Redis access, not model loading. Corpus indexing
was also running, so these are demonstration samples rather than controlled
performance benchmarks. One other query's total search became slower despite a
faster cached embedding: cross-encoder and CPU contention still dominate overall work.

## Controlled embedding outage, real lexical corpus

`make search ARGS='--query DivOPD --repeat 4 --fail-dense'` injects an exception at
the dense provider boundary; it does not stop or damage any container.

| Request | Mode | Circuit after request | Reason | Evidence hits | Total ms |
|---:|---|---|---|---:|---:|
| 1 | bm25_only | closed | dense_unavailable | 5 | 173.328 |
| 2 | bm25_only | closed | dense_unavailable | 5 | 112.621 |
| 3 | bm25_only | open | dense_unavailable | 5 | 192.231 |
| 4 | bm25_only | open | dense_circuit_open | 5 | 0.547 |

The first three requests exhausted bounded retries. The fourth skipped the failed
provider entirely. All returned keyword evidence, not generated answers. A one-word
query such as DivOPD can rank numeric tables highly; fallback preserves availability,
not equivalent semantic quality.

## Automated checks

- Fake-clock tests verify closed/open/half-open transitions and one recovery probe.
- Injected provider tests verify bounded retries, jitter math and skipped calls.
- Redis failure and malformed-payload tests verify fail-open embedding computation.
- Embedder tests verify query/document reuse and separate late-pooling identities.
- The opt-in real Redis test verifies TTL expiration and avoided provider work with
  an intentionally delayed fixture. That fixture latency is not a model benchmark.
- Real pgvector tests verify cosine ranking, snapshot filtering, HNSW availability,
  replacement idempotency and transaction rollback using uniquely named test tables.

Run `CHUNKFORGE_RUN_PGVECTOR_TESTS=1 CHUNKFORGE_RUN_REDIS_TESTS=1 make check`.
CI provisions separate PostgreSQL and Redis services. No flush-all operation is used.
