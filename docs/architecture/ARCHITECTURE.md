# ChunkForge Architecture

This directory is the version-controlled architecture record for ChunkForge. The
diagrams deliberately separate **implemented behavior** from the **target design**.
That distinction is essential: an architecture diagram must describe the system
honestly, not make future modules look production-ready.

## Diagram catalog

| Diagram | Question it answers | Current status |
|---|---|---|
| [End-to-end architecture](end-to-end.md) | What are the major components and how do they collaborate? | Foundation, batch ingestion and chunking implemented |
| [Document ingestion](ingestion-flow.md) | How does a PDF become searchable chunks and vectors? | Batch download/parse, chunking and persistence implemented; events planned |
| [Search and RAG generation](search-rag-flow.md) | How does a question become a ranked, cited answer? | Target for Phases 4–6 and 8–9 |
| [Retrieval internals](retrieval-internals.md) | How do dense search, BM25, RRF, and reranking interact? | Implemented CLI; four-paper real-model demonstration |
| [Resilience](resilience-flow.md) | What happens when Redis or dense retrieval fails? | Implemented cache, retries, breaker and evidence fallback |
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

Phases 0 through 2 currently provide:

- typed environment configuration in `chunkforge/config.py`;
- a runnable FastAPI service with `GET /health` in `chunkforge/api/main.py`;
- PostgreSQL/pgvector, Redis, Kafka, and Ollama in `docker-compose.yml`;
- the multi-stage API image in `Dockerfile.api`;
- linting, strict typing, tests, and CI;
- rate-limited, resumable arXiv acquisition with a metadata manifest;
- conservative PDF text cleaning with provenance and measured batch statistics;
- five chunking strategies with source offsets and configurable token budgets;
- BGE sentence similarity and passage-local contextual token pooling;
- a reproducible five-paper chunk comparison with source hashes.

Phase 3's embedding wrapper, transactional pgvector store and HNSW indexes now
have a verified one-paper pilot; the full 200-paper index remains pending.
Hybrid retrieval and embedding-dependency resilience are implemented and verified
through the CLI. Measured examples and limitations are in
[the retrieval guide](../HYBRID_RETRIEVAL.md). Event-driven ingestion, HTTP search/ask
routes, generation and Angular remain planned.

## Documentation boundary

The diagrams document ChunkForge's design without relying on private schemas,
internal URLs, credentials, employer-specific identifiers, or proprietary assets.
