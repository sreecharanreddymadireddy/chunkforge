# Architecture decisions and alternatives

Choices optimize learning transparency, reproducibility and this corpus/hardware,
not universal superiority. Evidence is linked from `eval/`; proposals aren't results.

| Decision                           | Why this choice                                            | Alternative / when it wins                                                    | Cost or corner case                                                           |
| ---------------------------------- | ---------------------------------------------------------- | ----------------------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| uv + pyproject + lock              | Explicit extras and reproducible environment               | pip/requirements for simpler installs; Poetry for its workflow                | Sync can remove unselected ML extras during active jobs                       |
| Python/FastAPI                     | Direct model ecosystem plus typed HTTP                     | Spring Boot can orchestrate separate model services                           | Python hints aren't Java runtime enforcement; CPU/GIL behavior needs care     |
| Explicit libraries                 | Inspect tokenization, RRF, prompts and failure handling    | LangChain/LlamaIndex integrate many providers faster                          | More wiring to maintain; frameworks don't eliminate validation                |
| PyMuPDF layout parsing             | Source offsets, speed and block access                     | OCR/document intelligence for scanned/complex layouts                         | Tables, math and ambiguous columns remain imperfect                           |
| arXiv corpus                       | Real long-form technical text, resumable source IDs        | Internal authorized documents for domain validity                             | Licensing/selection bias; public papers don't prove enterprise relevance      |
| 75indexed /200downloaded           | Manage laptop CPU/storage; same lifecycle learning         | Larger benchmark for statistically broader evidence                           | Smaller corpus can't prove 10M-document behavior                              |
| Five chunk strategies              | Compare mechanisms, not just tune a single splitter        | Structure-aware/code/table/adaptive/query-aware strategies                    | Unequal core budgets confound pure strategy attribution                       |
| 512core/50fixed overlap            | Visible baseline under controlled budgeting                | Smaller cores improve precision; larger preserve context                      | Counting tokenizer differs from embedding tokenizer                           |
| Passage-local late pooling         | Retain contextual token representations within BGE limits  | Long-context late chunking with suitable model                                | Not whole-document context;128core differs from 512baseline                   |
| BGE-small 384d CPU                 | Local affordable model and small vectors                   | Larger/domain embeddings may improve retrieval                                | Domain shift, lexical precision and multilingual limits                       |
| Normalize vectors                  | Stable cosine comparison; validate finite dimensions       | Other metrics for appropriately trained spaces                                | Normalization can't make incompatible model spaces compatible                 |
| pgvector/PostgreSQL                | Transactions, source metadata and vectors together         | Qdrant for dedicated vector features; Pinecone for managed ops                | Index memory/DB IO; not a claim Postgres wins at every scale                  |
| HNSW                               | Incremental approximate navigation; no IVF training step   | Exact search for small corpus; IVFFlat when partition/training trade-offs fit | Memory/build cost, filter recall; measure against exact neighbors             |
| In-memory rank-bm 25               | Direct visible lexical scoring                             | PostgreSQL full-text/Elastic for durable shared lexical indexing              | Per-process memory and rebuild costs; not a scalable distributed BM25 service |
| Handwritten RRF k60                | Merge ranks without arbitrary raw-score normalization      | Learned/weighted fusion with independent labels                               | Discards score magnitude; k is a choice, not a quality guarantee              |
| Bi-encoder then cross-encoder 20→5 | Reusable vectors then expensive pairwise relevance         | Cross-encode all at tiny scale; skip rerank for strict latency                | Truncation/window aggregation biases; can't recover missing candidates        |
| Redis embedding cache              | Avoid repeated inference, model-aware identity/TTL         | In-process cache for one process; answer cache with corpus-aware invalidation | Redis hop/serialization costs; cache isn't semantic truth or encryption       |
| Retry + jitter + breaker           | Bound transient work and stop repeated dependency pressure | No retries for non-idempotent/invalid work                                    | Retries inflate tail latency; process-local breaker isn't global coordination |
| BM25 fallback                      | Preserve evidence when dense fails                         | Fail-fast if semantic quality is mandatory                                    | Needs warm snapshot; degraded quality must be visible                         |
| Ollama +1b local model             | No paid core API; inspect HTTP prompt/results              | Larger local model or authorized hosted provider                              | CPU latency/completeness; cache and small context don't cure hallucination    |
| Explicit JSON/citation validation  | Reject unknown IDs/truncation; retain evidence             | Structured generation plus entailment checking                                | Valid ID isn't evidence support; application suffix is formatting only        |
| Conservative byte context proxy    | Simple, auditable clipping with source ranges              | Exact generator tokenizer budgeting                                           | Wastes capacity; approximation is not formal all-model token accounting       |
| Source-span relevance labels       | Stable evidence target across chunk strategies             | Graded relevance judgments with multiple annotators                           | 80%coverage threshold and first-occurrence quote lookup bias metrics          |
| Angular SPA                        | Existing strength, expandable contracts/compare UX         | Streamlit for quickest internal prototype                                     | Node toolchain, DTO drift and UI test maintenance                             |
| One inference slot                 | Bound CPU/model contention on 8 GB laptop                  | Queued serving + scaled model workers                                         | 429/admission pressure; not per-user fairness/distributed limiting            |
| Kafka for ingestion                | Durable buffering, replay and decoupling                   | Batch for static corpus; lighter task queue for simpler jobs                  | Single broker isn't HA; file path events aren't cloud object integration      |
| Per-strategy DB transaction        | Safe replay/resume without duplicate completed work        | Global transaction/outbox/versioned corpus snapshots                          | Five-strategy atomicity and concurrent writer ordering aren't guaranteed      |
| ECS Fargate target                 | Small operational footprint vs owning cluster nodes        | EKS for Kubernetes ecosystem; EC2 GPU for model serving                       | CPU model cost/cold starts; co-task coupling; deployment not executed         |
| RDS + S3 target                    | Managed relational durability plus object backups          | Dedicated vector/object data plane at larger scale                            | Access, restore, retention and cost must be configured/tested                 |

## What would change the architecture?

More corpus alone doesn't justify agents or fine-tuning. First measure where
recall drops, which latency stage dominates, index/cache memory, ingestion backlog
and tenant-security requirements. At10M documents: estimate chunks/vector/index
storage, separate model serving/indexing, replace per-process lexical snapshots,
add tenant/ACL filters before retrieval, benchmark ANN recall, and measure capacity
under load before selecting sharding or a dedicated vector platform.
