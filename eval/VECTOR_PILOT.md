# Vector indexing pilot

Scope: one full processed paper, `2609.34838v1`, indexed across five strategies.
This is a Phase 3 integration check, not the full 200-paper completion result.

Model: `BAAI/bge-small-en-v1.5` at revision
`5c38ec7c405ec4b44b94cc5a9bb96e735b38267a`; 384 dimensions, CPU inference.
PostgreSQL 16 with pgvector 0.8.0; one partial HNSW cosine index per strategy.
Source hash and chunking parameters are available in `CHUNKING_RESULTS.json`.

Query: `How does DivOPD choose turns when training agents?`

| Strategy | Stored chunks | Warm search p50 (ms) |
|---|---:|---:|
| fixed | 54 | 12.131 |
| recursive | 55 | 12.112 |
| semantic | 163 | 12.631 |
| sentence_window | 895 | 12.255 |
| late | 221 | 14.688 |

Each median uses 20 repetitions after a warm-up. Timing includes PostgreSQL
connection establishment and retrieval, excluding model loading, query encoding
and generation. These are local single-client measurements, not load-test results.

Manual inspection found relevant passages describing rollout allocation and
selection by teacher/student disagreement. Rankings also contained general
background material; no relevance labels, recall scores or quality winner are
claimed. Retrieval quality remains a later evaluation task.

The database contains 1,388 chunk rows. A separate integration test verifies
known numeric cosine scores, a usable HNSW query plan, idempotent replacement,
and transaction rollback. PostgreSQL may choose a sequential scan for small
tables; index existence does not imply every pilot query used HNSW.

Standard oversized cores use an explicit average of bounded BGE piece embeddings.
Late cores retain their passage-local token-mean vectors and use token-mean query
pooling. This pooling path is experimental with BGE and has not demonstrated a
quality advantage. Sentence windows retain surrounding context separately.

Reproduce a pilot and query:

```bash
make embed ARGS='--max-documents 1 --query "How does DivOPD choose turns when training agents?"'
```

Re-running with unchanged source, settings and model revision skips committed
document/strategy runs. An incomplete or failed transaction is never marked
complete. Run the full Phase 3 corpus with `make embed`; the default is 200
documents across all five strategies. That full run is still pending.
