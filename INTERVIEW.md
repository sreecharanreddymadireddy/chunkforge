# ChunkForge — senior engineering interview defense

These are senior-system-design preparation questions. Defend only
what the code and measured artifacts show. AWS deployment and final 40-label
evaluation remain explicit gates, not achievements to claim prematurely.

## Two-minute project explanation

“I built ChunkForge to investigate how document boundaries affect retrieval, not
just produce a PDF chatbot. I downloaded and parsed 200 arXiv papers and indexed 75
through five chunking strategies with source-preserving offsets. BGE produces
normalized 384-dimensional vectors stored in PostgreSQL/pgvector with HNSW. Queries
combine dense search and BM25, merge ranks using RRF, then cross-encode 20 candidates
to return 5 passages. The Angular UI exposes evidence and independent scores.

“For answers, FastAPI packs bounded numbered evidence into an explicit local
Ollama prompt, validates JSON/citation IDs and returns a cited answer, abstention
or explicit evidence-only degradation. Redis caches embeddings using model-aware
keys; retries, jitter, circuit breaking and keyword fallback bound failures. Kafka
handles document ingestion, not synchronous questions; writes precede offset
commit and fingerprints make completed strategy replays safe.

“I measured a ten-question pilot on the same 60 documents for each strategy.
Recursive led with recall@5=0.95 and MRR@5=0.625, but that small source-concentrated
pilot isn't independent proof of generalization. I verified actual model calls,
PostgreSQL/Redis integration, a Kafka replay and the UI. The remaining final labels
and cloud execution are clearly separated from completed local capabilities.”

## Ten-minute walkthrough

1. State problem and constraints: source evidence, laptop 8 GB, transparent libraries,
   no paid core API, measurable comparison. Don't begin with a list of tools.
2. Draw two lifecycles: document→index; question→retrieve→generate. Kafka is only
   in the former. PostgreSQL is durable evidence, Redis is reconstructable vectors.
3. Show one source: DivOPD recursive chunk 3,359 counter tokens, `[6901,8682)`.
4. Show one request and current source scores in END_TO_END_GUIDE. Distinguish
   raw scoring scales and candidate selection from generated language.
5. Show prompt clipping to `[6901,7597)`, actual answer, citation ID validation,
   and why a valid citation can still accompany an incomplete answer.
6. Show controlled outage/replay and one measured missed label; describe limitations.
7. Close with the next measured bottleneck and production-hardening plan, not a
   claim every managed service is already deployed.

## Architecture end to end (1–5)

### 1. What problem does RAG solve here, and what doesn't it solve?

It supplies current corpus evidence at inference time without changing model
weights. It doesn't guarantee correct parsing, relevant retrieval or truthful
generation. Example: the DivOPD answer cites real evidence but omits filtering and
disagreement details. I inspect retrieval recall and answer support separately.
**Probe:** Why not fine-tune? Fine-tuning changes behavior/weights and needs suitable
training/evaluation; it doesn't substitute for auditable fresh source retrieval.

### 2. Trace one request across every dependency.

Angular sends `{query,strategy}`; FastAPI validates and admits one heavy request.
It refreshes lexical snapshots if DB manifests changed, runs BM25, resolves the
query embedding from Redis/BGE, searches pgvector, fuses ranks and reranks. Search
returns evidence; Ask packs that same retrieval snapshot and calls Ollama. Kafka
isn't called. **Probe:** A cache hit skips BGE inference, not all database/model work.

### 3. Why Python/FastAPI when you know Java?

Model/tokenizer libraries are directly accessible; typed contracts, constructor
injection and explicit boundaries preserve familiar Spring design principles.
`Protocol` is structural typing; dataclasses are record-like; Pydantic validates
runtime DTOs. Python hints don't enforce every value automatically. **Probe:**
Synchronous routes use FastAPI's thread pool; CPU inference doesn't become free
because the framework supports async.

### 4. Where is state, and what survives a restart?

PostgreSQL stores text/vectors/manifests; files preserve PDF/text/provenance; Kafka
stores ingestion records/group offsets; Redis caches vectors; Ollama volume stores
weights. Ordinary Compose down preserves volumes, but process-local BM25/breakers
are rebuilt/reset. Example: DB outage with a warm BM25 snapshot can degrade; a
cold process can't build that snapshot. **Probe:** A volume isn't disaster recovery.

### 5. What consistency model do users observe during ingestion?

Each document/strategy replacement is transactional. The API refreshes its lexical
snapshot from changed manifests, but the entire corpus/five strategies aren't one
atomic snapshot and concurrent commits can race with snapshot construction.
Example: Compare while indexing can see unequal document availability. Evaluation
freezes common IDs to reduce this confound. **Probe:** Versioned immutable corpus
snapshots are needed for stronger cross-service consistency.

## Chunking (6–11)

### 6. Why chunk instead of embed an entire paper?

One vector can dilute a narrow fact across many topics, and models have finite
input capacity. Small source spans improve targeting, but too-small spans lose
referents like “this method.” Example: DivOPD chunk 3 retains the coverage/focus
explanation and source coordinates. **Probe:** More chunks aren't inherently better;
they increase indexing cost and can crowd top-k with redundant evidence.

### 7. Fixed token windows: why overlap 50 and budget 512?

They're explicit baselines, not optimized constants. Overlap carries boundary
context but repeats text/vectors and can reduce result diversity. Example:512/50
usually advances about 462 units, with Unicode/re-tokenization edge cases handled
by source-safe boundaries. **Probe:** 512 tiktoken units aren't necessarily 512 BGE
tokens; the backend enforces the actual model input limit separately.

### 8. When does recursive splitting win?

When paragraph/sentence structure captures coherent evidence, recursive refinement
preserves it before packing bounded chunks. It avoids arbitrary cuts without extra
semantic model calls. The measured coverage/focus label appears in recursive
chunk 3 at rank 2 in the pilot, while fixed missed that span. **Probe:** This isn't
proof of universal superiority; malformed PDF boundaries and unequal budgets matter.

### 9. How does semantic chunking choose boundaries?

Encode adjacent sentences and split where similarity falls below a configured
threshold, respecting core limits. It can separate topic transitions missed by
surface punctuation, at extra inference cost. Example: threshold 0.5 produced 163
DivOPD chunks in the chunking comparison. **Probe:** Topic similarity isn't task
relevance; changing domains/tokenization can change useful thresholds.

### 10. What is sentence-window retrieval, and is expansion actually used?

Index a sentence-sized anchor and carry nearby source context separately. It can
retrieve a precise fact and later supply the missing referent. Our metadata
preserves ±1 context, but current generation uses core text, not expanded windows.
Example:895 DivOPD anchors versus 55 recursive chunks. **Probe:** Never claim the
potential expansion benefit as a measured generation improvement in this v1.

### 11. Why is late chunking different from embedding split text?

It first computes contextual token vectors, then pools the vectors belonging to
each smaller core. Neighboring passage text can influence those token vectors.
Our implementation uses BGE-sized passages and 128-token cores, not entire-paper
attention. **Probe:** Its lower pilot score doesn't disprove all late chunking;
model context, pooling, core sizes and labels all differ.

## Embeddings and similarity (12–15)

### 12. What does a 384-dimensional embedding mean?

It is a learned coordinate representation optimized for semantic relationships,
not 384 human-labeled attributes or a reversible document encoding. The DivOPD
query begins `[-0.08019,-0.03527,0.05659,...]`; no coordinate independently means
“DivOPD.” **Probe:** Sensitive text may leak through derived representations;
hashing keys doesn't make vectors private or anonymous.

### 13. Derive cosine similarity and explain normalization.

Cosine is dot product divided by vector lengths. `[1,0]` and `[1,1]` yield
`1/sqrt(2)≈0.707`. Normalize to unit length, then dot product equals cosine.
pgvector cosine distance is 1−similarity. **Probe:** Reject zero/nonfinite/wrong-width
vectors; normalization can't reconcile embeddings from incompatible model spaces.

### 14. Why distinguish query and document encoding?

Retrieval models can be trained with query instructions/roles. We prepend BGE's
query instruction and include role/representation/model revision in cache identity.
Documents are stored once; queries are encoded per request or cache hit. **Probe:**
Sharing model name without revision/pooling metadata risks comparing incompatible
representations, especially normal core versus late token-mean pooling.

### 15. Dense versus sparse: what are their complementary failures?

Dense representations capture paraphrases but may blur exact IDs/numbers/negation.
Sparse lexical scoring preserves matching terms but misses vocabulary mismatch.
Example: QiYao-M and Endo-Modality are useful exact anchors for BM25. **Probe:**
Hybrid helps coverage, but neither path is a logical verifier of “not,” dates or
contradictory claims; evaluate those cases explicitly.

## Vector storage (16–19)

### 16. Why pgvector over Qdrant or Pinecone?

It co-locates metadata, source text, vector search and transactional completion
markers using familiar SQL. Qdrant offers dedicated vector-serving features;
Pinecone trades infrastructure control for managed operations. For75docs Postgres
is sufficient, not universally best. **Probe:** Choose based on measured scale,
filter semantics, operational ownership and recall/latency—not brand preference.

### 17. Explain HNSW without hand-waving.

HNSW builds a multi-layer navigable proximity graph. Search moves toward promising
neighbors, then explores a bounded candidate region. It approximates nearest
neighbors instead of exhaustively comparing every row. **Probe:** Increasing search
effort generally trades latency for recall; index memory/build effort and filters
matter. Validate against exact search, not only sensible-looking hits.

### 18. HNSW versus IVFFlat versus exact search?

Exact search is a strong small-corpus baseline. IVFFlat partitions around trained
centroids and probes selected partitions; HNSW avoids an initial clustering step
but needs graph memory/build work. **Probe:** Distribution shifts, update patterns,
filter selectivity and workload determine the trade-off; our pilot hasn't proven
the best ANN configuration or a scale threshold.

### 19. How do you avoid duplicate indexing and partial writes?

Fingerprint source hash, strategy/config and model identity. Replace one document/
strategy and its completion marker transactionally; unchanged complete runs skip.
A failure rolls back that strategy. Example: Kafka replay left 1/1/1/11/5 fixture
chunks unchanged. **Probe:** This doesn't provide ordering between simultaneous
writers or atomic replacement of all 5 strategies; add version/fencing controls.

## Hybrid ranking (20–22)

### 20. Explain the BM25 score instead of saying “keyword search.”

It combines term rarity with saturating term frequency and length normalization.
Repeated rare matches help, but repeating a term 100 times doesn't produce 100×gain.
In our historical QiYao trace, rare exact terms contributed strongly to score
38.6868753. **Probe:** Tokenization and corpus-average length affect scores; a score
isn't comparable across corpora or a probability of relevance.

### 21. Calculate RRF and defend it against alternatives.

For dense rank 8 and BM25 rank 3, k60 gives 1/68+1/63=0.0305788982. It combines rank
agreement without normalizing unrelated raw scores. Duplicate passage identity is
counted at most once per list. **Probe:** RRF ignores score magnitude; weighted or
learned fusion could win with independent labels. Stable tie breaking aids debugging.

### 22. Bi-encoder versus cross-encoder, and where can reranking fail?

Bi-encoder vectors are independent/reusable and fast for candidate retrieval.
Cross-encoder jointly reads query+candidate for richer relevance, so we apply it
only to 20 and return 5. Output logits aren't probabilities. **Probe:** It can't
recover an absent candidate; long-passage window maxima can overvalue one matching
fragment, and a reranker can worsen useful evidence ordering.

## Grounding and generation (23–25)

### 23. What exactly is sent to the generator?

Visible system instructions, JSON-numbered source evidence and a JSON-serialized
question, plus bounded context/output options. Real DivOPD chunk 3 is clipped from
`[6901,8682)` to `[6901,7597)` for the prompt. **Probe:** 1495 bytes isn't1495 tokens;
we use a conservative byte proxy and reserve template/output capacity, not exact
Llama token accounting.

### 24. Do citations and temperature 0 prevent hallucination?

No. Valid citation IDs establish references to supplied passages, not that each
claim follows from them. Temperature0 reduces sampling randomness, not unsupported
reasoning. Our answer passed structural checks yet omitted source mechanisms.
**Probe:** Add answer-faithfulness/completeness evaluation, contradiction tests and
claim-to-evidence review; don't market a formatting validator as an entailment model.

### 25. What happens on unsupported questions or invalid model JSON?

No usable evidence yields canonical abstention; the model can also declare
insufficiency. Invalid/truncated response, unknown/conflicting IDs or transport
failure yield evidence-only output. Example: the private-access-code question
really abstained. A missing inline marker with valid supplied IDs is formatted
deterministically and labeled. **Probe:** Prompt injection and reliable refusal
still require adversarial evaluation; one smoke test isn't a refusal metric.

## Evaluation (26–28)

### 26. Why source-span labels instead of relevant chunk IDs?

The same fact lands in different chunks per strategy. Exact quotes locate stable
source spans; a hit covering≥80% of a labeled span counts as recovered evidence.
**Probe:** This threshold can penalize smaller anchors, and repeated quotes resolve
to first occurrence. A stronger benchmark needs reviewed span disambiguation and
possibly graded judgments/multiple acceptable evidence locations.

### 27. Work through recall@5 and MRR with a multi-hop case.

If two passages are required and only one appears at rank 2, recall=0.5 and RR=0.5.
If both appear at ranks 2and4, recall=1 but RR remains 0.5. Average RR across questions
for MRR. **Probe:** First supporting hit doesn't prove all reasoning hops or a
correct generated answer; report retrieval and answer metrics separately.

### 28. Why can't you call recursive the universal winner?

The real pilot used 10 seed questions concentrated in 3 papers over 60 common documents.
Recursive scored 0.95/0.625, but labels, core budgets and pipeline interactions
confound broad claims. **Probe:** Thirty independently authored questions are still
required; freeze data/config, don't tune on held-out labels, inspect misses and
measure uncertainty rather than reporting only a flattering mean.

## Reliability (29–32)

### 29. What exactly is in Redis and how do you invalidate it?

A versioned hashed key identifies model/revision, query/document role, pooling,
dimension and input text. Value is normalized vector JSON with TTL. A model/input
identity change yields a different key. Example:384 coordinates, configured 86400s.
**Probe:** It isn't an answer cache; answer caching would also need corpus/prompt/
generator identity, authorization isolation and stale-answer policy.

### 30. Which failures should retry, and how do you bound latency?

Retry operational failures with capped exponential backoff+jitter, not blank queries
or corrupt model dimensions. The breaker wraps the exhausted request. **Probe:**
Every attempt adds latency and load; idempotency matters for writes. Current local
embedding calls aren't isolated behind hard subprocess deadlines, so retry count
alone isn't a comprehensive end-to-end latency bound.

### 31. Explain closed/open/half-open and its concurrency limitations.

Closed attempts calls; after threshold failures open short-circuits; after recovery
time half-open permits a probe, closing on success. The injected outage opened on
request 3; request 4skipped dense work. **Probe:** State is process-local, resets on
restart and isn't fleet-wide failure coordination. The one-slot API gate reduces
local contention but doesn't create a global breaker.

### 32. What is graceful degradation versus hiding errors?

Keep useful evidence while reporting lost capability. Dense outage returns
`bm25_only`; reranker outage `hybrid_unreranked`; generation failure `evidence_only`.
**Probe:** HTTP200 isn't full health. Cold lexical startup still needs DB access;
fallback quality can be poor, so a domain requiring semantic guarantees might
choose fail-fast instead.

## Streaming ingestion (33–34)

### 33. Defend at-least-once processing across Kafka and PostgreSQL.

Broker delivery precedes processing; DB strategy transactions precede synchronous
offset commit. Crash after DB success/before commit replays; fingerprints skip
completed work. The real fixture replay kept counts unchanged. **Probe:** Producer
idempotence doesn't atomically commit Postgres and Kafka. Exactly-once across them
would need stronger coordination/outbox/inbox/version design, not a slogan.

### 34. What happens to poison events, downtime and lag?

Invalid payload/path/source identity is acknowledged to the DLQ before input commit.
Operational failures leave offsets uncommitted and exit. Consumer downtime retains
records until retention expiry; lag counts unread positions, not documents/sec.
**Probe:** The event references a shared PDF path; deleting/changing that file before
processing breaks the contract. Cloud needs durable object keys and versions.

## Deployment and scaling (35–38)

### 35. What is actually deployed, and where?

API/UI run on the Mac; Postgres/Redis/Kafka/Ollama run in Docker Desktop. Source and
CI are on GitHub. No AWS application or public URL is verified. The AWS kit renders
operator-specific ECS/IAM documents and an execution runbook. **Probe:** Don't
substitute “deployment-ready configuration” for a real cloud deployment claim.

### 36. How do IAM, networking and secrets fit together?

Execution role pulls images/injects the DSN/writes logs; task role controls app
AWS calls. SGs allow ALB→frontend 80 and task→RDS5432, not public Redis/Ollama/DB.
Secrets stay outside images/Git; TLS and DB grants remain separate requirements.
**Probe:** IAM permission alone doesn't create network reachability, DB privileges
or hostname-verified TLS.

### 37. What breaks first at 10 million documents?

Estimate chunks/doc, then vector/index/text storage:10Mdocs×20 chunks×384×4 bytes
is 307.2 GB of raw float32 coordinates alone, before indexes/metadata/replicas. That's
an illustrative 20 chunks/doc assumption, not our observed average. Per-process
BM25/model memory, ingestion throughput and DB working set fail much earlier.
**Probe:** Measure filters/ANN recall, separate serving/indexing, then choose sharding.

### 38. How would you scale ingestion and serving independently?

Use immutable/versioned object references, partitioned document events, supervised
workers and idempotent storage. Serve shared lexical/vector indexes with separate
model endpoints and bounded queues/admission. **Probe:** More consumers don't help
when DB/model CPU is saturated; partition count caps useful group parallelism,
and stale updates need version ordering/fencing before concurrent writers are safe.

## Cost and performance (39–40)

### 39. Where does latency come from, and what would you optimize first?

Measure cold model load, manifest/BM25 build, cache, query encode, vector search,
fusion, cross-encoder, prompt packing and LLM prefill/decode separately. Cached
embedding can improve while total request slows under CPU contention. **Probe:**
The154s isolated local generation isn't an API percentile. Optimize the dominant
measured stage, preserve retrieval quality, then load-test p50/p95/p99.

### 40. What costs matter beyond model API tokens?

Embedding recomputation, chunk proliferation, index memory, reranking CPU, model
RAM, storage/backups, network transfer, managed DB/ALB/NAT and idle cloud resources.
Local inference avoids paid-provider requests but isn't free compute. **Probe:**
Compare cost per successful supported answer at an agreed quality/latency target,
not only per-token price or one favorable cached request.

## Difficulties actually encountered — implementation evidence

| Difficulty                        | Cause and implementation response                                    | Evidence / limitation                                                                 |
| --------------------------------- | -------------------------------------------------------------------- | ------------------------------------------------------------------------------------- |
| Port5432 occupied                 | Publish PostgreSQL host 5433; preserve internal 5432/DSN distinction | Local connection verified; not a DB inside API container                              |
| SQL entered at shell prompt       | Exec shell isn't psql; enter database client first                   | OPERATIONS has exact prompt/query flow                                                |
| VS Code pip inspection error      | uv environment needn't contain pip                                   | Use uv; don't rebuild a working environment merely for pip                            |
| Tokenizer mismatch                | tiktoken and BGE measure different units                             | Backend enforces real model windows; Unicode/source-span tests                        |
| Slow all-strategy indexing        | CPU inference and many sentence anchors                              | Reduced target 75, kept 200raw; fingerprints allow resume                             |
| Local model too large             | 8GBhost/3.8GiBDocker constrained original model                      | Approved1b model, smaller prompt/output, bounded parallelism                          |
| Model omitted inline marker       | Valid citation_ids but missing `[1]` in text                         | Validated suffix formatting; unknown/conflicting IDs still rejected                   |
| Invalid output/timeouts           | Small CPU model and strict protocol                                  | Evidence-only state; no invented answer or claimed latency improvement                |
| Kafka logs outside mounted volume | Default log directory didn't match mount                             | Stopped broker, backed up/copied data, corrected path, verified topics                |
| Smoke test stuck before publish   | Sampled process blocked reading source PDF                           | Used bounded locally rendered real-text fixture; exact storage-layer cause not proven |
| esbuild aborted in sandbox        | Build failed under restricted execution                              | Bounded-worker elevated retry passed; don't claim a proven application deadlock fix   |

### A truthful STAR example

Situation: Kafka replay/durability verification needed real persistent broker state.
Task: demonstrate ingestion without duplicate chunks and protect existing metadata.
Action: inspected logs/volume paths, found the mismatch, stopped/backed up the broker,
copied its data into the actual named volume, corrected log.dirs and verified topic
recovery. Then published a PDF event with no consumer, started the worker and
replayed it after success. Result:5 strategy writes completed, committed offset 1
and lag 0 after the first event; replay left counts unchanged. Scope: local smoke
verification, not a multi-broker HA deployment or production outage reduction.

## Live interview demo, five minutes

1. Show README boundaries and 75-per-strategy SQL counts.
2. Search the main DivOPD question; inspect source range and separate scores.
3. Compare two strategies, keeping question constant.
4. Show recorded prompt/raw reply/citation formatting; run Ask if latency permits.
5. Show pilot misses, fallback trace and Kafka replay evidence; explain next gaps.

Only claim independently verified capabilities and results. A public deployment,
statistically proven winner requires separate evidence. Explain
the code you understand and can change under follow-up questioning.
