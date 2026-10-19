"""Lazy BGE access for sentence similarity and bounded contextual token vectors."""

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ContextTokens:
    """Character-aligned contextual vectors for one model-sized source passage."""

    start: int
    end: int
    offsets: tuple[tuple[int, int], ...]
    vectors: tuple[tuple[float, ...], ...]


class SentenceEncoder(Protocol):
    """Small dependency contract, like constructor-injecting a Java service."""

    def encode(self, texts: list[str]) -> list[tuple[float, ...]]:
        """Return one normalized vector per supplied sentence."""


class ContextEncoder(Protocol):
    """Supply token vectors without hiding truncation or passage boundaries."""

    model_name: str

    def contextualize(self, text: str) -> list[ContextTokens]:
        """Encode the full source as explicitly bounded contextual passages."""


def normalized_mean(vectors: list[tuple[float, ...]]) -> tuple[float, ...]:
    """Average columns and scale the result to unit length; reject malformed data."""
    if not vectors or not vectors[0]:
        raise ValueError("At least one nonempty vector is required")
    width = len(vectors[0])
    if any(len(v) != width for v in vectors):
        raise ValueError("Embedding dimensions differ")
    mean = [sum(v[i] for v in vectors) / len(vectors) for i in range(width)]
    norm = sum(x * x for x in mean) ** 0.5
    if norm == 0:
        raise ValueError("Cannot normalize a zero vector")
    return tuple(x / norm for x in mean)


class BGEBackend:
    """Use SentenceTransformer directly; retain its transformer for late pooling.

    Documents are tokenized without truncation, then partitioned into passages
    fitting the model's own tokenizer budget including its special tokens.
    Semantic sentence vectors use the model's normal pooling. Late vectors use
    mean pooling over transformer token outputs, an intentionally different path.
    """

    def __init__(self, model_name: str, batch_size: int = 32, device: str = "cpu") -> None:
        from sentence_transformers import SentenceTransformer

        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        self.model_name = model_name
        self.batch_size = batch_size
        self.model: Any = SentenceTransformer(model_name, device=device, trust_remote_code=False)
        self.transformer: Any = self.model[0]
        self.tokenizer: Any = self.transformer.tokenizer
        self.limit = int(self.model.max_seq_length) - int(
            self.tokenizer.num_special_tokens_to_add(pair=False),
        )
        if self.limit < 1 or not self.tokenizer.is_fast:
            raise ValueError("A fast tokenizer with a positive source-token budget is required")

    def _passages(self, text: str) -> list[tuple[int, int]]:
        """Revalidate substrings: WordPiece tokenization can change after a cut."""
        if not text.strip():
            return []
        pending = [(0, len(text))]
        result: list[tuple[int, int]] = []
        while pending:
            start, end = pending.pop()
            encoded = self.tokenizer(
                text[start:end],
                add_special_tokens=False,
                return_offsets_mapping=True,
                verbose=False,
            )
            offsets = encoded["offset_mapping"]
            if len(offsets) <= self.limit:
                result.append((start, end))
                continue
            cut = start + int(offsets[self.limit][0])
            if not start < cut < end:
                raise ValueError("Embedding token budget cannot fit a source character")
            pending.extend([(cut, end), (start, cut)])
        return result

    def encode(self, texts: list[str]) -> list[tuple[float, ...]]:
        """Encode bounded sentence pieces; average pieces for oversized sentences."""
        spans = [self._passages(text) for text in texts]
        pieces = [text[a:b] for text, ranges in zip(texts, spans, strict=True) for a, b in ranges]
        if any(not ranges for ranges in spans):
            raise ValueError("Sentence encoder received text without model tokens")
        if not pieces:
            return []
        encoded = self.model.encode(
            pieces,
            batch_size=self.batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        rows = [tuple(float(x) for x in row) for row in encoded]
        result: list[tuple[float, ...]] = []
        offset = 0
        for ranges in spans:
            result.append(normalized_mean(rows[offset : offset + len(ranges)]))
            offset += len(ranges)
        return result

    def contextualize(self, text: str) -> list[ContextTokens]:
        """Return actual transformer outputs, excluding padding and special tokens."""
        import torch

        result: list[ContextTokens] = []
        for start, end in self._passages(text):
            encoded = self.tokenizer(
                text[start:end],
                return_tensors="pt",
                return_offsets_mapping=True,
                truncation=False,
            )
            offsets = encoded.pop("offset_mapping")[0].tolist()
            if encoded["input_ids"].shape[1] > self.model.max_seq_length:
                raise ValueError("Passage exceeds model context; refusing silent truncation")
            inputs = {key: value.to(self.model.device) for key, value in encoded.items()}
            with torch.inference_mode():
                hidden = self.transformer.auto_model(**inputs).last_hidden_state[0].cpu().tolist()
            pairs = [
                (a, b, vector) for (a, b), vector in zip(offsets, hidden, strict=True) if b > a
            ]
            result.append(
                ContextTokens(
                    start,
                    end,
                    tuple((start + int(a), start + int(b)) for a, b, _ in pairs),
                    tuple(tuple(float(x) for x in vector) for _, _, vector in pairs),
                )
            )
        return result
