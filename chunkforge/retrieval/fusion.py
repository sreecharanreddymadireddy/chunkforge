"""Reciprocal Rank Fusion without comparing unrelated raw score scales."""

from dataclasses import dataclass

from chunkforge.store.pgvector_store import SearchResult


@dataclass(frozen=True, slots=True)
class RankedHit:
    """Evidence with separate stage scores; none is a calibrated confidence."""

    chunk: SearchResult
    dense_score: float | None = None
    bm25_score: float | None = None
    rrf_score: float | None = None
    rerank_score: float | None = None


def reciprocal_rank_fusion(
    dense: list[SearchResult],
    lexical: list[SearchResult],
    k: int = 60,
) -> list[RankedHit]:
    """Sum 1/(k + one-based rank); count a duplicate at most once per input list."""
    if k < 1:
        raise ValueError("RRF k must be positive")
    evidence: dict[tuple[str, int], SearchResult] = {}
    scores: dict[tuple[str, int], float] = {}
    stage_scores: list[dict[tuple[str, int], float]] = []
    for ranking in (dense, lexical):
        seen: dict[tuple[str, int], float] = {}
        for rank, chunk in enumerate(ranking, start=1):
            key = (chunk.doc_id, chunk.chunk_index)
            if key in seen:
                continue
            seen[key] = chunk.score
            evidence.setdefault(key, chunk)
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
        stage_scores.append(seen)
    return [
        RankedHit(evidence[key], stage_scores[0].get(key), stage_scores[1].get(key), scores[key])
        for key in sorted(scores, key=lambda key: (-scores[key], key))
    ]
