# ChunkForge

ChunkForge is a retrieval-augmented generation (RAG) workbench for measuring how
document-processing and chunking choices affect retrieval quality. The current
release implements a reproducible local environment and a production-style batch
ingestion pipeline for arXiv machine-learning papers.

## Current capabilities

- Resumable, rate-limited arXiv acquisition with an auditable JSONL manifest.
- Streaming PDF downloads with signature validation, SHA-256 hashing, and atomic writes.
- Layout-aware PDF parsing with repeated-margin removal, page-number filtering,
  conservative dehyphenation, and two-column ordering.
- Clean-text output with page-level character spans and parsing statistics.
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

The ingestion path is implemented. Chunking, embedding persistence, hybrid
retrieval, grounded answer generation, and the Angular comparison interface are
documented in the architecture roadmap and will be added incrementally.

Detailed diagrams are available in the
[`docs/architecture`](docs/architecture/ARCHITECTURE.md) catalog.

## Requirements

- Python 3.11 or newer
- Docker Desktop, or Docker Engine with the Compose plugin
- GNU Make

Node.js is not required until the Angular client is introduced.

## Quick start

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

The current suite contains eight tests covering configuration, API behavior, Atom
metadata parsing, rate limiting, resumable downloads, PDF cleaning, two-column
ordering, provenance, and parser restartability. The verified suite has 86% overall
statement coverage.

## Repository structure

```text
chunkforge/
├── chunkforge/api/             FastAPI application
├── chunkforge/ingestion/       arXiv downloader and PDF parser
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

1. Five chunking strategies with measured token and metadata behavior.
2. Batched BGE embeddings and PostgreSQL/pgvector HNSW search.
3. Dense + BM25 retrieval, reciprocal-rank fusion, and cross-encoder reranking.
4. Redis caching, retry, circuit-breaker, and BM25-only fallback behavior.
5. Grounded Ollama generation with citations and an evaluation harness.
6. FastAPI search/answer contracts and an Angular comparison interface.
7. Kafka-based continuous ingestion and an AWS ECS deployment guide.
