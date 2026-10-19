# Hybrid retrieval demonstration

Measured on 2026-10-19 using real local BGE embeddings, PostgreSQL/pgvector,
`rank-bm25` and `cross-encoder/ms-marco-MiniLM-L6-v2`.
Reranker revision: `233902d25c440f23af6f7d6e94d2946bac0bee0a`.
Strategy: recursive. Snapshot: four documents, 244 chunks. The full 200-document
index was still building; these are scoped demonstrations, not corpus-wide benchmarks.

Both retrieval paths used the same document set. Dense and lexical each requested
50 candidates; RRF used k=60, reranking considered 20, and output contained five.
The following source passages were inspected directly in PostgreSQL.

| Query | Selected supporting passage | Dense top-5 rank | Hybrid final rank |
|---|---|---|---:|
| How does DivOPD remove dead rows? | `2609.34838v1:3`: removes turns without usable supervision | Absent | 3 |
| What is Endo-Modality Proxy Training in QiYao-M? | `2609.34842v1:4`: proxy training supports adaptation without updating TSFM parameters | Absent | 2 |
| How does HyperReservoir use a bilinear readout? | `2609.34847v1:5`: context modulates the readout while recurrent dynamics stay fixed | Absent | 4 |

These show improved placement of specific supporting passages, not that every dense
result was irrelevant. Passage labels were selected during exploratory inspection;
they are not a held-out test set and must not be presented as recall@5 or MRR.
Other retrieved passages can also support the same answer.

## A failure worth keeping

For the dead-row question, hybrid rank one was chunk 40, a formula-heavy appendix
passage. The clearer introductory explanation was rank three. For “What is
Exo-Multimodal Retrieval Enhancer?”, both methods ranked a diagram-text chunk first.
Reranking is therefore not automatically a win; PDF ordering and evidence quality
still matter. Long-passage maximum-window scoring can also reward a partially
matching window and needs evaluation against other aggregation methods.

## Timing scope

For the dead-row question, the first search took 1986.215 ms and the repeat took
1579.053 ms while corpus indexing competed for CPU. Model loading and BM25 snapshot
construction were excluded. These are individual samples, not a latency percentile
or a service-level objective. See the resilience report for the separately measured
embedding-cache stage.

Reproduce with the commands in [the retrieval guide](../docs/HYBRID_RETRIEVAL.md).
The document set grows as indexing completes, so rerunning later can change rankings.
