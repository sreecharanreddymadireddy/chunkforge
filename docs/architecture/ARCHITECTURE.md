# ChunkForge Architecture

This directory is the version-controlled architecture record for ChunkForge. The
diagrams deliberately separate **implemented behavior** from the **target design**.
That distinction is essential: an architecture diagram must describe the system
honestly, not make future modules look production-ready.

## Diagram catalog

| Diagram | Question it answers | Current status |
|---|---|---|
| [End-to-end architecture](end-to-end.md) | What are the major components and how do they collaborate? | Local application implemented; see measured verification below |
| [Document ingestion](ingestion-flow.md) | How does a PDF become searchable chunks and vectors? | Batch and Kafka ingestion implemented; replay smoke-tested |
| [Search and RAG generation](search-rag-flow.md) | How does a question become a ranked, cited answer? | Local API/UI and generation implemented; target details remain explicitly scoped |
| [Retrieval internals](retrieval-internals.md) | How do dense search, BM25, RRF, and reranking interact? | CLI/API implemented; ten-question evaluation pilot measured |
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
   A file-watcher producer and Kafka consumer decouple heavy background work.
2. **Search and answer generation:** the API checks the cache, retrieves and
   reranks evidence, and asks Ollama to generate a cited answer.

Kafka belongs to asynchronous ingestion. It is not placed in the synchronous
search path. Redis is an embedding cache, not an answer or job-state store; PostgreSQL with pgvector is
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

The 75-paper v1 index is complete for all five strategies. The downloaded
200-paper corpus is retained locally for later expansion. Hybrid retrieval,
resilience, Ollama generation, HTTP search/ask, Angular comparison and Kafka
ingestion are implemented. The measured retrieval pilot freezes 60 common
documents; the final benchmark still requires thirty independent user labels.
See [live verification and explicit gaps](../../eval/PHASES_6_TO_10.md).
Older target diagrams may show broader schema/deployment details; their arrows
are not evidence of an implemented endpoint or persistence contract. The current
API has no upload/job-status endpoint, and Redis does not cache final answers.

## Documentation boundary

The diagrams document ChunkForge's design without relying on private schemas,
internal URLs, credentials, employer-specific identifiers, or proprietary assets.
