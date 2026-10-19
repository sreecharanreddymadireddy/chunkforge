"""Preserve lexical evidence when the vector dependency is unavailable."""

from chunkforge.retrieval.fusion import RankedHit
from chunkforge.store.pgvector_store import SearchResult


def keyword_fallback(matches: list[SearchResult], limit: int) -> list[RankedHit]:
    """Return honest keyword-only scores, never synthetic dense or reranker scores."""
    if limit < 1:
        raise ValueError("Limit must be positive")
    return [RankedHit(chunk=chunk, bm25_score=chunk.score) for chunk in matches[:limit]]
