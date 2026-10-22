# ChunkForge

## Engineering guide

- [Visual phase 0–14 atlas](docs/PROJECT_ATLAS.html): open the standalone file in a browser.
- [End-to-end data journey](docs/END_TO_END_GUIDE.md): actual tokens, vectors, chunk records and scores.
- [API contracts and flow](docs/API_FLOWS.md) · [Run/debug/deployment inventory](docs/OPERATIONS.md).
- [Design decisions and alternatives](docs/DESIGN_DECISIONS.md) · [40 senior interview Q&A](INTERVIEW.md).
- [AWS deployment kit](aws/DEPLOY.md) · [Phase closure and gap audit](docs/GAP_AUDIT.md).

**Live locally, not publicly deployed.** The final evaluation requires thirty
independently authored questions; AWS execution requires your account/network/cost
choices. No public endpoint or final held-out benchmark is claimed.

ChunkForge is a retrieval-augmented generation (RAG) workbench for measuring how
document-processing and chunking choices affect retrieval quality. The current
release implements a reproducible local environment, a batch ingestion pipeline
for arXiv machine-learning papers, a five-strategy chunking lab, and inspectable
hybrid retrieval with dependency resilience, grounded local generation, an Angular
comparison interface, and replay-safe Kafka ingestion.

## Current capabilities

- Resumable, rate-limited arXiv acquisition with an auditable JSONL manifest.
- Streaming PDF downloads with signature validation, SHA-256 hashing, and atomic writes.
- Layout-aware PDF parsing with repeated-margin removal, page-number filtering,
  conservative dehyphenation, and two-column ordering.
- Clean-text output with page-level character spans and parsing statistics.
- Fixed-token, recursive, semantic, sentence-window, and passage-local late chunking.
- Source-preserving chunk offsets, token budgets, and contextual pooled vectors.
- Batched, normalized BGE embeddings and transactional pgvector persistence.
- Strategy-specific HNSW indexes, resumable indexing, and a verified one-paper vector-search pilot.
- BM25 + dense retrieval, hand-written RRF, and cross-encoder reranking (50 -> 20 -> 5).
- Redis embedding caching, bounded jittered retries, circuit breaking and keyword-only fallback.
- Typed FastAPI `/search` and `/ask` routes, liveness and OpenAPI docs.
- Ollama answers with validated citation IDs and explicit abstention/evidence-only states.
- Angular search, answer and side-by-side strategy comparison screens.
- Kafka PDF events, manual consumer commits, dead-letter handling and replay-safe indexing.
- A source-passage-labeled evaluation harness and a measured ten-question pilot.
- Local PostgreSQL/pgvector, Redis, Kafka, and Ollama services through Docker Compose.
- Ruff linting, strict mypy checks, pytest coverage, and GitHub Actions CI.

## Architecture

```text
arXiv API -> validated PDFs -> layout-aware parser -> clean text + provenance
                                                        |
                                                        v
PDFs -> chunks -> embeddings -> pgvector ----------- dense retrieval --+
             +----------------> BM25 --------------- sparse retrieval --+-> fuse -> rerank
Question -> query embedding --------------------------------------------+          |
                                                                                   v
Angular UI <- FastAPI JSON <- cited answer <- Ollama + retrieved evidence
```

The local 75-paper index is complete across all five strategies. Generation, API,
Angular and Kafka flows have live smoke-test evidence; see
[Phases 6–10 verification](eval/PHASES_6_TO_10.md). The
[retrieval pilot](eval/RESULTS.md) freezes a common 60-document corpus and uses ten
seed questions. Thirty independently authored labels remain required before the
final forty-question benchmark. See [retrieval limitations](docs/HYBRID_RETRIEVAL.md).

Detailed diagrams are available in the
[`docs/architecture`](docs/architecture/ARCHITECTURE.md) catalog.

## Requirements

- Python 3.11 or newer
- Docker Desktop, or Docker Engine with the Compose plugin
- GNU Make

Use Node.js 26 with the Angular 22 client. The API can run without the frontend.

## Quick start

See [Run and test](docs/RUN_AND_TEST.md) for the complete command flow, isolated
smoke tests, service checks, and current implementation boundaries.

```bash
cp .env.example .env
make setup
make check
make docker-up
make docker-ps
```

`make setup` bootstraps a project-local `uv` installation and synchronizes the
locked development dependencies. Start the API with:

```bash
make api
```

Then open <http://localhost:8000/docs> or request the health endpoint:

```bash
curl http://localhost:8000/health
```

## Local services

| Service | Purpose | Default host endpoint |
|---|---|---|
| PostgreSQL + pgvector | Document, chunk, metadata, and vector storage | `localhost:5432` |
| Redis | Reconstructable embedding cache | `localhost:6379` |
| Kafka | Event-driven ingestion broker | `localhost:9092` |
| Ollama | Local language-model server | `localhost:11434` |

The Compose stack uses named volumes, so ordinary container replacement does not
delete database, broker, cache, or model state.

## Chunking lab

After ingesting the corpus, compare all five strategies on five complete papers:

```bash
make chunk-compare
```

The command installs the ML extra and downloads the public BGE-small model on its
first run. It writes [measured chunk statistics](eval/CHUNKING_RESULTS.md) and an
adjacent JSON report with source hashes, model revision, and parameters. Model
weights are not needed for normal unit tests. The first tokenizer load downloads
and caches tiktoken's public vocabulary.

Fixed windows default to 512 tokens with 50 overlap. Recursive splitting prefers
paragraphs and sentences with zero overlap. Semantic splitting compares adjacent
sentence embeddings. Sentence-window chunks keep indexed anchors separate from
surrounding context. Late chunks retain mean-pooled contextual token vectors.

BGE-small has a 512-model-token limit: late chunking therefore uses separate
bounded passages and 128-token cores, not whole-document context. tiktoken core
counts and BGE tokenizer limits are checked separately. Chunk counts are not
retrieval scores, and different core budgets preclude a same-size quality claim.

Parameters are configured with `CHUNKFORGE_CHUNK_SIZE`, `CHUNKFORGE_CHUNK_OVERLAP`,
`CHUNKFORGE_SEMANTIC_THRESHOLD`, `CHUNKFORGE_SENTENCE_WINDOW_SIZE`,
`CHUNKFORGE_LATE_CHUNK_SIZE`, `CHUNKFORGE_TOKEN_ENCODING`,
`CHUNKFORGE_EMBEDDING_BATCH_SIZE`, and `CHUNKFORGE_EMBEDDING_DEVICE`.

## Vector indexing pilot

With PostgreSQL running, index one full paper and inspect real vector search:

```bash
make embed ARGS='--max-documents 1 --query "How does DivOPD choose turns when training agents?"'
```

See [pilot measurements and limitations](eval/VECTOR_PILOT.md). Index the selected 75
papers with `make embed ARGS='--max-documents 200'`, or use `make embed` for the
75-paper v1 target; unchanged completed document/strategy runs are skipped.
The job records source/config/model fingerprints and atomically commits chunks
with their completion manifest. Late vectors retain their pooling convention.

Run the local PostgreSQL integration tests explicitly:

```bash
CHUNKFORGE_RUN_PGVECTOR_TESTS=1 make check
```

CI provides an isolated pgvector service. Local integration tests allocate and
remove only their own uniquely named test tables.

## Hybrid search and failure drill

```bash
make search ARGS='--query "What is Endo-Modality Proxy Training in QiYao-M?" --strategy recursive --repeat 2'
make search ARGS='--query DivOPD --repeat 4 --fail-dense'
CHUNKFORGE_RUN_PGVECTOR_TESTS=1 CHUNKFORGE_RUN_REDIS_TESTS=1 make check
```

The first command loads local BGE and cross-encoder models, builds a BM25 snapshot,
and prints scored evidence. The second deliberately fails the dense provider;
keyword evidence remains available and the circuit opens after exhausted calls.
No containers are stopped by the drill. Model weights download on the first run.

[Hybrid examples](eval/HYBRID_RESULTS.md) record three supporting-passage improvements
and failure cases, not held-out quality metrics. [Resilience results](eval/RESILIENCE_RESULTS.md)
record real cache timings and a controlled outage. CI also provisions Redis and
tests cache expiry using uniquely named keys; it never flushes unrelated data.

## Batch ingestion

Run the complete default corpus job:

```bash
make ingest
```

For a small validation run:

```bash
.venv/bin/python -m chunkforge.ingestion.downloader --max-results 5
.venv/bin/python -m chunkforge.ingestion.parser
```

The downloader follows arXiv's published three-second request interval. Downloads
land in `data/raw`; normalized text, provenance sidecars, and aggregate statistics
land in `data/processed`. Both corpus directories are excluded from Git.

### Verified corpus run

| Measurement | Result |
|---|---:|
| Documents | 200 |
| Pages | 5,666 |
| Clean text characters | 18,142,649 |
| Average characters per document | 90,713.2 |
| Document failures | 0 |

The parser preserves native text by default. Opt-in Tesseract OCR handles low-text
pages dominated by a raster scan, with page-level extraction provenance, language
pack/configuration-aware caching, and page/pixel budgets. OCR is off by default;
semantic table reconstruction, handwriting and general multimodal understanding
remain outside the verified scope. See [OCR setup and five-document lab](docs/OCR.md).

## Configuration

Configuration is loaded from `CHUNKFORGE_*` environment variables through a typed
Pydantic settings model. Copy `.env.example` for safe local defaults. The resulting
`.env` file is ignored and must never be committed.

The same application artifact can therefore use localhost endpoints during local
development, Docker service names inside Compose, and managed-service endpoints in
a hosted environment without source changes.

## Quality checks

```bash
make lint
make typecheck
make test

# Run every quality gate
make check
```

The suite covers configuration, API behavior, rate limiting, resumable downloads,
PDF cleaning and provenance, all five chunking strategies, model-token boundary
validation, Unicode safety, overlap, contextual pooling, and report generation.
CI uses deterministic encoder fixtures; the corpus comparison uses the actual BGE model.

## Repository structure

```text
chunkforge/
├── chunkforge/api/             FastAPI application
├── chunkforge/ingestion/       arXiv downloader and PDF parser
├── chunkforge/chunking/        five strategies and reproducible comparison
├── data/raw/                   ignored source PDFs
├── data/processed/             ignored text and provenance output
├── docker/postgres/            pgvector initialization
├── docs/architecture/          implemented and target architecture diagrams
├── tests/                      pytest suite
├── docker-compose.yml          local dependency topology
├── Dockerfile.api              multi-stage API image
├── pyproject.toml              package and tool configuration
└── uv.lock                     reproducible dependency lock
```

## Run the application

```sh
docker compose exec ollama ollama pull llama3.2:1b
make api
# Separate terminal, first run only: cd frontend && npm ci
make ui
```

Visit <http://localhost:4200> and <http://localhost:8000/docs>. The first request
loads retrieval models. Local CPU generation can take minutes; the UI reports
timeouts and evidence-only output explicitly. Redis caches embeddings, not answers.
Kafka processes PDF ingestion, not synchronous user questions.

```sh
make eval ARGS='--pilot --documents 60'
make kafka-watch
# Separate terminal:
make kafka-consume
```

Start the watcher before adding a new PDF. Its initial baseline skips existing
files. See [complete commands and boundaries](docs/RUN_AND_TEST.md).

## Remaining execution gates

The documentation, forty-question interview bank and explicit gap audit are now
present. The AWS task/IAM renderer and deployment guide are prepared, but cloud
execution is pending. Thirty independently authored labels still gate the final
benchmark. The [closure ledger](docs/GAP_AUDIT.md) records each phase precisely.
The optional all-container application profile has not been load-tested on this
8 GB laptop; host API/UI plus container dependencies is the verified local mode.
