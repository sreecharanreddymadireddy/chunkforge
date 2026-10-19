"""Direct cross-encoder inference with explicit pair-window handling."""

import math
from dataclasses import replace
from typing import Any, Protocol

from chunkforge.retrieval.fusion import RankedHit


class PairScorer(Protocol):
    """Like a Java interface for an injectable inference provider."""

    def score(self, query: str, passages: list[str]) -> list[float]:
        """Return one finite relevance logit per passage."""


class CrossEncoderScorer:
    """Score bounded query/passage windows and keep each passage's best window.

    Unlike implicit tokenizer truncation, every passage token is considered.
    Query tokens are bounded explicitly; oversized questions are rejected.
    Window-max aggregation is a v1 heuristic, not a relevance probability.
    """

    def __init__(self, model_name: str, device: str = "cpu", batch_size: int = 16) -> None:
        import torch
        from sentence_transformers import CrossEncoder

        if batch_size < 1:
            raise ValueError("Batch size must be positive")
        self.model: Any = CrossEncoder(
            model_name,
            device=device,
            max_length=512,
            trust_remote_code=False,
            activation_fn=torch.nn.Identity(),
        )
        self.batch_size = batch_size
        self.model_id = model_name + "@" + str(self.model.model.config._commit_hash)

    def score(self, query: str, passages: list[str]) -> list[float]:
        """Window text using tokenizer offsets; let prediction batch the bounded pairs."""
        tokenizer = self.model.tokenizer
        question = tokenizer(query, add_special_tokens=False, verbose=False)["input_ids"]
        if len(question) > 128:
            raise ValueError("Reranker question exceeds the explicit 128-token budget")
        budget = 512 - len(question) - tokenizer.num_special_tokens_to_add(pair=True)
        pairs: list[tuple[str, str]] = []
        groups: list[tuple[int, int]] = []
        for passage in passages:
            pending = [passage]
            start = len(pairs)
            while pending:
                piece = pending.pop()
                offsets = tokenizer(
                    piece, add_special_tokens=False, return_offsets_mapping=True, verbose=False
                )["offset_mapping"]
                if len(offsets) > budget:
                    cut = int(offsets[budget][0])
                    if not 0 < cut < len(piece):
                        raise ValueError("Cannot fit passage character in reranker window")
                    pending.extend([piece[cut:], piece[:cut]])
                else:
                    pairs.append((query, piece))
            groups.append((start, len(pairs)))
        if not pairs:
            return []
        values = self.model.predict(pairs, batch_size=self.batch_size, show_progress_bar=False)
        return [max(float(v) for v in values[a:b]) for a, b in groups]


class Reranker:
    """Keep candidate identity and all earlier stage scores while replacing rank order."""

    def __init__(self, scorer: PairScorer) -> None:
        self.scorer = scorer

    def rerank(self, query: str, candidates: list[RankedHit], limit: int = 5) -> list[RankedHit]:
        """Score core text (not expanded sentence context), validate and return top-k."""
        if limit < 1:
            raise ValueError("Limit must be positive")
        if not candidates:
            return []
        scores = self.scorer.score(query, [hit.chunk.text for hit in candidates])
        if len(scores) != len(candidates) or not all(math.isfinite(s) for s in scores):
            raise ValueError("Reranker returned invalid scores")
        ranked = [replace(hit, rerank_score=s) for hit, s in zip(candidates, scores, strict=True)]
        return sorted(
            ranked,
            key=lambda hit: (
                -float(hit.rerank_score or 0),
                hit.chunk.doc_id,
                hit.chunk.chunk_index,
            ),
        )[:limit]
