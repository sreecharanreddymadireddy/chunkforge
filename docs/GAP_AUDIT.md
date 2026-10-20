# Phase 14 — Explicit gap audit

“Implemented” means code exists; “verified” names the test/measurement scope.
Neither implies production certification. No missing user task is silently marked done.

## Phase closure ledger

| Phase              | Status                                     | Evidence / remaining gate                                                |
| ------------------ | ------------------------------------------ | ------------------------------------------------------------------------ |
| 0 environment      | Local complete                             | Compose dependencies healthy; typed config, CI                           |
| 1 batch ingestion  | Complete for selected public corpus        | 200cleaned PDFs; parsing stats                                           |
| 2 chunking         | Implemented/measured                       | 5 strategies,5 paper comparison, source/token checks                     |
| 3 vector storage   | 75-document v1 complete                    | 75completed runs per strategy; HNSW/schema/transaction checks            |
| 4 hybrid retrieval | Implemented/live examples                  | Dense/BM25/RRF/reranker traces; not universal quality proof              |
| 5 resilience       | Implemented/scoped verification            | Redis, injected outages, retry/breaker transitions                       |
| 6 generation       | Implemented/live smoke                     | Real cited response + refusal; no broad faithfulness benchmark           |
| 7 evaluation       | Harness/pilot complete; final blocked      | 10 seed labels; user must independently author 30more                    |
| 8 API              | Implemented/verified                       | Search/Ask/health/docs, real requests, validation/error tests            |
| 9 Angular          | Implemented/verified                       | Build/tests, actual search/compare, mobile smoke                         |
| 10 Kafka           | Implemented/local verified                 | Retained-event/replay checks; single broker/shared files                 |
| 11 AWS             | Deployment kit prepared; execution pending | Renderer/IAM/runbook; no provisioned resources or URL verified           |
| 12 documentation   | Consolidated                               | Atlas, end-to-end, API, ops, decisions and measured reports              |
| 13 interview bank  | 40model Q&A prepared                       | Understanding still requires your self-test, not a generated certificate |
| 14 audit           | This ledger complete                       | Revisit after real AWS run/final labels, not silently promote statuses   |

## Consequence → v2 work → proof needed

| Simplification / omission                        | Consequence                                                       | Next implementation / verification                                                                                    |
| ------------------------------------------------ | ----------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| Time-dependent default download query            | A later clone can fetch different sources from the recorded pilot | Versioned-source bootstrap is documented; add pinned-manifest reacquisition/hash checks for exact corpus reproduction |
| 75indexed,200downloaded                          | Small/source-biased retrieval workload                            | Larger authorized corpus, held-out tasks and repeatable baselines                                                     |
| No OCR/semantic table reconstruction             | Missing or garbled evidence                                       | Layout/OCR route, curated scan/table tests and source review                                                          |
| Heuristic sentence/column segmentation           | Abbreviations/references/math can split badly                     | More structure-aware parser, measured error corpus                                                                    |
| Different core budgets per strategy              | Not isolated causal comparison of splitter only                   | Same-budget ablation + coverage/latency/storage curves                                                                |
| Passage-local late pooling                       | No global long-document context                                   | Appropriate long-context embedding model and fair comparison                                                          |
| Sentence window not expanded in generation       | Potential context benefit unused                                  | Budgeted dedup/window packing and answer-quality comparison                                                           |
| Model/pooling partitions, no migration framework | Upgrade/reindex can disrupt serving                               | Dual-version indexes, backfill, shadow reads and atomic switch                                                        |
| Per-process BM25 snapshot                        | Memory/rebuild cost; cold DB dependency                           | Shared persisted lexical service/PostgresFTS; freshness tests                                                         |
| Approximate search not broad ANN-recall study    | Sensible hits don't quantify missed neighbors                     | Exact-search baseline across filters/query slices                                                                     |
| RRF fixedk 60, top 50/20/5                       | Defaults may hide recall/latency trade-offs                       | Independent-label parameter sweeps; don't tune on final test                                                          |
| Window-max cross-encoder scoring                 | Fragment match may dominate                                       | Alternate aggregation/length handling evaluated per slice                                                             |
| Seed labels concentrated in 3sources             | Pilot isn't generalization evidence                               | User-authored 30 labels, reviewed spans and independent holdout                                                       |
| First occurrence exact quote +80%span rule       | Ambiguity and short-core bias                                     | Disambiguated page/span IDs, graded relevance, adjudication                                                           |
| Retrieval metrics only                           | Correct citations can accompany wrong/incomplete answer           | Faithfulness/completeness/refusal/citation-precision metrics                                                          |
| Byte proxy for generator context                 | Conservative under-use; not formal token proof                    | Exact model tokenizer/chat-template accounting                                                                        |
| Small1b CPU generator                            | Slow/incomplete answers; low capacity                             | Measure stronger/local-hosted models under budget/quality targets                                                     |
| Structural citation validation only              | Doesn't establish entailment                                      | Claim-evidence validation and human-reviewed adversarial set                                                          |
| No query rewriting, HyDE or multi-query          | Complex/ambiguous questions may miss evidence                     | Add only after baseline error analysis; measure added latency/hallucination                                           |
| No agents/LangGraph/fine-tuning                  | No tool planning/learned domain adaptation                        | Add only for demonstrated task need; not required for baseline RAG                                                    |
| Cache only embeddings                            | Repeat answers still rerank/generate                              | Answer cache only with corpus/model/prompt/ACL identity and stale policy                                              |
| No cache stampede protection across replicas     | Concurrent misses can duplicate work                              | Distributed single-flight/leases, bounded wait and load tests                                                         |
| Process-local breaker/one-slot semaphore         | Not fleet-wide capacity/fairness                                  | Per-dependency serving limits, queues and tenant budgets                                                              |
| Retry count without universal call deadlines     | Slow native inference can exceed expectations                     | End-to-end budgets, cancellation/isolation and timeout tests                                                          |
| No tenant/ACL/OAuth2/JWT                         | Unsafe to expose sensitive/public service                         | Enforce source authorization before retrieval, audit and abuse controls                                               |
| No Vault integration                             | External secrets management is not implemented                    | Integrate only if deployment requires it; verify secret delivery and rotation                                         |
| No dependency readiness route                    | Healthy liveness can still fail search                            | Lightweight capability readiness separate from liveness/warmup                                                        |
| Sparse request tracing/SLO instrumentation       | Hard to quantify production p95/error budgets                     | Correlation IDs, stage histograms, safe logs, dashboards/alerts                                                       |
| No load/chaos/security audit                     | Unit/live smoke isn't fleet reliability                           | Rate/concurrency/memory tests, controlled failure drills and threat review                                            |
| Query ValueError mapped 422                      | Some config errors look like client errors                        | Domain error types and tighter sanitized HTTP contracts                                                               |
| Extra request fields not forbidden               | Schema drift can be silently ignored                              | Deliberate strict DTO policy and versioned contracts                                                                  |
| Concurrent DB writers unfenced                   | Older/newer events can race                                       | Version checks/advisory locks or serialized per-document ownership                                                    |
| Per-strategy, not global transaction             | Partial5 strategy availability                                    | Corpus/version snapshot protocol for atomic comparison                                                                |
| Kafka single broker/local files                  | No HA/distributed file durability                                 | Managed/multi-node broker, immutable S3version keys and worker role                                                   |
| Producer state based on size/mtime               | Rewrites/timing races can evade watcher detection                 | Explicit completion events/content version semantics                                                                  |
| Operational worker failure exits                 | Needs operator restart/supervision                                | Backoff supervisor, alerting, retry/DLQ ownership and replay tools                                                    |
| No deletion/tombstone propagation                | Removed source can remain searchable                              | Delete events + cache/index invalidation and audit retention rules                                                    |
| Dependency ports bind broad interfaces locally   | LAN exposure risk without firewall                                | Loopback binding for local use; privateSGs in cloud                                                                   |
| Cloud artifacts not executed                     | No deployed endpoint/performance evidence                         | Operator account choices, costs, actual provision/smoke/restore/teardown                                              |
| Co-task Redis/Ollama ephemeral cloud target      | Cache loss, weight redownload and coupled scaling                 | Separate serving/cache, model mirror/persistence and measured availability                                            |
| DB backups not restored in cloud yet             | Backup policy alone doesn't prove recovery                        | Restore drill with source counts, index plan and answer checks                                                        |

## Verification boundary

This repository directly demonstrates local RAG ingestion, chunking, vector search,
hybrid ranking, resilience, generation, evaluation, API/UI and Kafka flow. It does
**not** establish production scale, enterprise security, high availability or
verified AWS operation. Only the measurements and verification artifacts in this
repository support capability claims.

## Final open inputs

1. Author30independent source-backed questions; run final 40-question benchmark.
2. Choose/approve AWS account, region, network and costs; execute the cloud guide.
3. Verify actual endpoint, resource inventory, logs, restoration and teardown; then
   update OPERATIONS with the real URL and deployment evidence.
4. Self-test the interview bank and modify code under a live follow-up question.

These gates cannot be completed by relabeling seeds, inventing credentials or
writing an endpoint into a document. Remaining engineering proposals above are
intentional v2 scope, not silently unfinished v1 stubs.
