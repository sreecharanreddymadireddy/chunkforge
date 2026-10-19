"""Dense + BM25 -> RRF -> pairwise reranking, with explicit dependency degradation."""

from dataclasses import dataclass
from typing import Protocol

import psycopg

from chunkforge.resilience.breaker import CircuitBreaker, CircuitOpen
from chunkforge.resilience.fallback import keyword_fallback
from chunkforge.resilience.retry import RetryPolicy
from chunkforge.retrieval.bm25 import BM25Index
from chunkforge.retrieval.fusion import RankedHit, reciprocal_rank_fusion
from chunkforge.retrieval.reranker import Reranker
from chunkforge.store.pgvector_store import STRATEGIES, SearchResult

DEPENDENCY_FAILURES = (OSError, RuntimeError, psycopg.OperationalError, psycopg.InterfaceError)


class DenseSearch(Protocol):
    """Inject a vector search callable without depending on a particular model host."""

    def __call__(self, query: str, strategy: str, limit: int) -> list[SearchResult]:
        """Return cosine-ranked evidence or raise an operational failure."""


@dataclass(frozen=True, slots=True)
class RetrievalResponse:
    """Return evidence, stage traces and degraded status; no answer-generation claim."""

    hits: list[RankedHit]
    mode: str
    reasons: tuple[str, ...]
    dense: list[SearchResult]
    lexical: list[SearchResult]
    fused: list[RankedHit]


class RetrievalPipeline:
    """Constructor injection mirrors Spring services; all expensive providers are explicit."""

    def __init__(
        self,
        dense: DenseSearch,
        lexical: BM25Index,
        reranker: Reranker,
        strategy: str,
        candidates: int = 50,
        rerank_candidates: int = 20,
        limit: int = 5,
        rrf_k: int = 60,
        retry: RetryPolicy | None = None,
        breaker: CircuitBreaker | None = None,
    ) -> None:
        if strategy not in STRATEGIES or not 1 <= limit <= rerank_candidates <= candidates <= 100:
            raise ValueError(
                "Require a known strategy and 1 <= limit <= rerank <= candidates <= 100"
            )
        if rrf_k < 1:
            raise ValueError("RRF k must be positive")
        self.dense_search = dense
        self.lexical = lexical
        self.reranker = reranker
        self.strategy = strategy
        self.candidates = candidates
        self.rerank_candidates = rerank_candidates
        self.limit = limit
        self.rrf_k = rrf_k
        self.retry = retry or RetryPolicy()
        self.breaker = breaker or CircuitBreaker()

    def search(self, query: str) -> RetrievalResponse:
        """Protect vector calls, retain lexical-only fallback and surface reranker failures."""
        if not query.strip():
            raise ValueError("Query cannot be blank")
        lexical = self.lexical.search(query, self.candidates)
        try:
            dense = self.breaker.call(
                lambda: self.retry.call(
                    lambda: self.dense_search(query, self.strategy, self.candidates),
                    DEPENDENCY_FAILURES,
                ),
                DEPENDENCY_FAILURES,
            )
        except CircuitOpen:
            return RetrievalResponse(
                keyword_fallback(lexical, self.limit),
                "bm25_only",
                ("dense_circuit_open",),
                [],
                lexical,
                [],
            )
        except DEPENDENCY_FAILURES:
            return RetrievalResponse(
                keyword_fallback(lexical, self.limit),
                "bm25_only",
                ("dense_unavailable",),
                [],
                lexical,
                [],
            )
        fused = reciprocal_rank_fusion(dense, lexical, self.rrf_k)
        try:
            hits = self.reranker.rerank(query, fused[: self.rerank_candidates], self.limit)
        except DEPENDENCY_FAILURES:
            return RetrievalResponse(
                fused[: self.limit],
                "hybrid_unreranked",
                ("reranker_unavailable",),
                dense,
                lexical,
                fused,
            )
        return RetrievalResponse(hits, "hybrid", (), dense, lexical, fused)
