# ChunkForge

ChunkForge is a retrieval-augmented generation (RAG) workbench for measuring how
document-processing and chunking choices affect retrieval quality. The current
release implements a reproducible local environment, a batch ingestion pipeline
for arXiv machine-learning papers, and a five-strategy chunking lab.

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
- Environment-driven FastAPI configuration and a liveness endpoint with OpenAPI docs.
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

Ingestion, chunking, and the vector-indexing foundation are implemented. The
Phase 3 pilot covers one full paper across all five strategies; the full
200-paper vector index is pending. Hybrid retrieval, answer generation, and
the Angular comparison interface remain in the roadmap.

Detailed diagrams are available in the
[`docs/architecture`](docs/architecture/ARCHITECTURE.md) catalog.

## Requirements

- Python 3.11 or newer
- Docker Desktop, or Docker Engine with the Compose plugin
- GNU Make

Node.js is not required until the Angular client is introduced.

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
| Redis | Reconstructable caches and job state | `localhost:6379` |
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

See [pilot measurements and limitations](eval/VECTOR_PILOT.md). Index all 200
papers with `make embed`; unchanged completed document/strategy runs are skipped.
The job records source/config/model fingerprints and atomically commits chunks
with their completion manifest. Late vectors retain their pooling convention.

Run the local PostgreSQL integration tests explicitly:

```bash
CHUNKFORGE_RUN_PGVECTOR_TESTS=1 make check
```

CI provides an isolated pgvector service. Local integration tests allocate and
remove only their own uniquely named test tables.

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

The parser intentionally reports ambiguity instead of inventing structure. OCR for
image-only pages and semantic table reconstruction are outside the current scope.

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

## Roadmap

1. Batched BGE embeddings and PostgreSQL/pgvector HNSW search.
2. Dense + BM25 retrieval, reciprocal-rank fusion, and cross-encoder reranking.
3. Redis caching, retry, circuit-breaker, and BM25-only fallback behavior.
4. Grounded Ollama generation with citations and an evaluation harness.
5. FastAPI search/answer contracts and an Angular comparison interface.
6. Kafka-based continuous ingestion and an AWS ECS deployment guide.
