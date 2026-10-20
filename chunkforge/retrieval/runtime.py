"""Explicit reusable model/store composition shared by API, evaluation and workers."""

from dataclasses import dataclass
from typing import cast

from redis import Redis
from redis.backoff import NoBackoff
from redis.retry import Retry

from chunkforge.chunking.model_backend import BGEBackend
from chunkforge.config import Settings
from chunkforge.embeddings.embedder import Embedder
from chunkforge.resilience.breaker import CircuitBreaker
from chunkforge.resilience.cache import CacheClient, EmbeddingCache
from chunkforge.resilience.retry import RetryPolicy
from chunkforge.retrieval.bm25 import BM25Index
from chunkforge.retrieval.pipeline import RetrievalPipeline
from chunkforge.retrieval.reranker import CrossEncoderScorer, Reranker
from chunkforge.store.pgvector_store import PgvectorStore, SearchResult


@dataclass
class SearchRuntime:
    """Own one model pair and Redis client; snapshot refresh is explicit per strategy."""

    settings: Settings
    embedder: Embedder
    store: PgvectorStore
    reranker: Reranker
    cache: EmbeddingCache
    client: Redis

    @classmethod
    def create(cls, settings: Settings) -> "SearchRuntime":
        """Load once rather than loading large models per API request."""
        backend = BGEBackend(
            settings.embedding_model, settings.embedding_batch_size, settings.embedding_device
        )
        revision = backend.transformer.auto_model.config._commit_hash
        if not revision:
            raise ValueError("Resolved embedding model revision is required")
        client = Redis.from_url(
            str(settings.redis_url),
            decode_responses=True,
            socket_timeout=settings.redis_timeout_seconds,
            socket_connect_timeout=settings.redis_timeout_seconds,
            retry=Retry(NoBackoff(), 0),
        )
        try:
            cache = EmbeddingCache(cast(CacheClient, client), settings.cache_ttl_seconds)
            embedder = Embedder(
                backend,
                settings.embedding_model + "@" + str(revision),
                settings.embedding_dimensions,
                settings.embedding_batch_size,
                settings.embedding_query_instruction,
                cache,
            )
            store = PgvectorStore(
                str(settings.postgres_dsn),
                settings.embedding_dimensions,
                settings.vector_table,
                settings.hnsw_ef_search,
            )
            model = settings.reranker_model.replace(
                "ms-marco-MiniLM-L-6-v2", "ms-marco-MiniLM-L6-v2"
            )
            return cls(
                settings,
                embedder,
                store,
                Reranker(CrossEncoderScorer(model, settings.embedding_device)),
                cache,
                client,
            )
        except BaseException:
            client.close()
            raise

    def pipeline(self, strategy: str, document_ids: list[str] | None = None) -> RetrievalPipeline:
        """Build a strategy-specific lexical snapshot from the same model partition."""
        chunks = self.store.lexical_snapshot(
            strategy, self.embedder.model_id, self.embedder.representation(strategy)
        )
        if document_ids is not None:
            selected = set(document_ids)
            chunks = [c for c in chunks if c.doc_id in selected]
        if not chunks:
            raise ValueError("No compatible indexed chunks for this strategy")
        doc_ids = sorted({c.doc_id for c in chunks})

        def dense(query: str, strategy: str, limit: int) -> list[SearchResult]:
            return self.store.search(
                self.embedder.embed_query(query, strategy),
                strategy,
                self.embedder.model_id,
                self.embedder.representation(strategy),
                limit,
                doc_ids,
            )

        s = self.settings
        return RetrievalPipeline(
            dense,
            BM25Index(chunks),
            self.reranker,
            strategy,
            s.retrieval_candidates,
            s.rerank_candidates,
            s.retrieval_limit,
            s.rrf_k,
            RetryPolicy(s.retry_attempts, s.retry_base_delay, s.retry_max_delay),
            CircuitBreaker(s.breaker_failure_threshold, s.breaker_recovery_seconds),
        )

    def close(self) -> None:
        """Release connection resources at application/CLI shutdown."""
        self.client.close()
