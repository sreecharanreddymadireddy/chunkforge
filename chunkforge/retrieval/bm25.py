"""In-memory BM25 over a strategy-specific, model-compatible corpus snapshot."""

import re
from dataclasses import replace
from typing import Any

from chunkforge.store.pgvector_store import SearchResult


def tokenize(text: str) -> list[str]:
    """Keep case-insensitive words, numbers and identifiers; no stemming or stopword removal."""
    return re.findall(r"\w+(?:[-.]\w+)*", text.casefold())


class BM25Index:
    """Build once per corpus snapshot; return only chunks sharing a query token.

    BM25Okapi weights term frequency, rarity and document length. Its scores are
    not probabilities and can be zero/negative on tiny or repetitive corpora.
    Rebuild after ingestion; this v1 index is process-local, not a distributed index.
    """

    def __init__(self, chunks: list[SearchResult]) -> None:
        from rank_bm25 import BM25Okapi

        identities = [(c.doc_id, c.chunk_index) for c in chunks]
        if len(set(identities)) != len(identities):
            raise ValueError("BM25 snapshot contains duplicate chunk identities")
        self.chunks = chunks
        self.tokens = [tokenize(c.text) for c in chunks]
        self.term_sets = [set(tokens) for tokens in self.tokens]
        self.index: Any = BM25Okapi(self.tokens) if chunks and any(self.tokens) else None

    def search(self, query: str, limit: int = 50) -> list[SearchResult]:
        """Deterministically rank matching chunks, including legitimate zero-score matches."""
        if not query.strip() or limit < 1:
            raise ValueError("Require a nonblank query and a positive limit")
        terms = tokenize(query)
        if self.index is None or not terms:
            return []
        scores = self.index.get_scores(terms)
        matches = [i for i, tokens in enumerate(self.term_sets) if tokens.intersection(terms)]
        matches.sort(
            key=lambda i: (-float(scores[i]), self.chunks[i].doc_id, self.chunks[i].chunk_index)
        )
        return [replace(self.chunks[i], score=float(scores[i])) for i in matches[:limit]]
