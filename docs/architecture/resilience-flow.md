# Resilience and degraded operation

**Status:** Implemented for CLI retrieval. This diagram describes embedding caching,
bounded dense-path retries, a process-local circuit breaker and evidence fallback.
Answer generation and Ollama failure handling are future work.

```mermaid
flowchart TD
    Q[Question] --> L[BM25 over loaded corpus snapshot]
    Q --> B{Dense circuit admission}
    B -->|open / probe busy| F[Keyword-only evidence]
    B -->|closed / admitted probe| R[Bounded retry: at most 3 attempts]
    R --> C{Redis embedding lookup}
    C -->|hit| V[Use cached query vector]
    C -->|miss / cache failure| E[BGE query encoding]
    E --> W[Best-effort Redis write with TTL]
    W --> V
    V --> P[pgvector top-50]
    P --> S[Successful logical call resets breaker]
    L --> U[RRF + top-20]
    S --> U
    U --> X[Cross-encoder]
    X -->|success| H[Hybrid top-5 evidence]
    X -->|operational failure| N[Fused evidence: hybrid_unreranked]
    R -->|exhausted operational failure| A[Count one breaker failure]
    A -->|threshold reached| O[Open for cooldown]
    A --> F
    L --> F
    O -->|cooldown ends on next call| T[Admit one half-open probe]
    T --> R
```

A cache hit bypasses query inference, **not** vector search, fusion or reranking.
The retry operation includes query embedding and database search. Its exhausted
failure counts once toward the default three-call breaker threshold. A failed
half-open probe reopens immediately; a successful one closes/reset failures.

## Cache policy

Only vectors are cached, not answers, search responses or job state. Keys use
`chunkforge:embedding:v1:<sha256>` over structured model-revision, pooling-role,
dimensions and exact prepared text. Default TTL is one day. Changed input or
identity misses immediately. Redis failures and corrupt entries fail open to
inference; a corrupt provider vector remains a validation error.

## Actual degraded contract

```json
{
  "mode": "bm25_only",
  "reasons": ["dense_circuit_open"],
  "hits": [],
  "dense": [],
  "lexical": [],
  "fused": []
}
```

Lists above are empty only to illustrate the response shape; real keyword matches
populate `hits` and `lexical`. Dense and reranker scores are absent in fallback.
No generated answer is fabricated. A cold process still requires initial model
loading and database snapshot access; this is warm-process graceful degradation.

## Scope

Retryable failures are classified operational Python/model and database connection
exceptions. There is no remote HTTP embedding service or rate-limit handler yet.
Redis has connect/read timeouts; database connection timeout is ten seconds.
Hard inference deadlines, distributed breakers, independent persistent lexical
snapshots and cache single-flight protection are not implemented.

See [full contracts and limitations](../HYBRID_RETRIEVAL.md),
[measured outage/cache results](../../eval/RESILIENCE_RESULTS.md), and
`tests/test_resilience.py` for deterministic state/concurrency tests.
