# ChunkForge Architecture

This directory is the version-controlled architecture record for ChunkForge. The
diagrams deliberately separate **implemented behavior** from the **target design**.
That distinction is essential: an architecture diagram must describe the system
honestly, not make future modules look production-ready.

## Diagram catalog

| Diagram | Question it answers | Current status |
|---|---|---|
| [End-to-end architecture](end-to-end.md) | What are the major components and how do they collaborate? | Phase 0 foundation and Phase 1 batch ingestion implemented |
| [Document ingestion](ingestion-flow.md) | How does a PDF become searchable chunks and vectors? | Batch download/parse implemented; Phases 2–3 and 10 planned |
| [Search and RAG generation](search-rag-flow.md) | How does a question become a ranked, cited answer? | Target for Phases 4–6 and 8–9 |
| [Retrieval internals](retrieval-internals.md) | How do dense search, BM25, RRF, and reranking interact? | Target for Phases 3–4 |
| [Resilience](resilience-flow.md) | What happens when Redis, embeddings, or Ollama fails? | Target for Phase 5 |
| [Local Docker topology](local-docker.md) | Which processes, ports, networks, and volumes exist locally? | Dependency stack implemented in Phase 0 |
| [AWS deployment](aws-deployment.md) | How does the local design map to managed AWS services? | Target for Phase 11 |

## Status legend

- **Implemented:** corresponding code or configuration exists and is testable.
- **Planned:** approved target design for a later build phase.
- **Conditional:** used only for the workflow shown, not on every request.

## The two primary workflows

ChunkForge is not one linear `UI → API → Kafka → database` chain. It has two
workflows that share storage and model services:

1. **Document ingestion:** documents are parsed, chunked, embedded, and persisted.
   Kafka decouples this heavy background work from the API.
2. **Search and answer generation:** the API checks the cache, retrieves and
   reranks evidence, and asks Ollama to generate a cited answer.

Kafka belongs to asynchronous ingestion. It is not placed in the synchronous
search path. Redis is operational state and cache; PostgreSQL with pgvector is
the durable retrieval system of record.

## Current implementation boundary

Phases 0 and 1 currently provide:

- typed environment configuration in `chunkforge/config.py`;
- a runnable FastAPI service with `GET /health` in `chunkforge/api/main.py`;
- PostgreSQL/pgvector, Redis, Kafka, and Ollama in `docker-compose.yml`;
- the multi-stage API image in `Dockerfile.api`;
- linting, strict typing, tests, and CI;
- rate-limited, resumable arXiv acquisition with a metadata manifest;
- conservative PDF text cleaning with provenance and measured batch statistics.

Chunking, embeddings/vector persistence, event-driven ingestion, retrieval,
generation, resilience, and Angular boxes remain planned until their phases add
working code and tests.

## Documentation boundary

The diagrams document ChunkForge's design without relying on private schemas,
internal URLs, credentials, employer-specific identifiers, or proprietary assets.
