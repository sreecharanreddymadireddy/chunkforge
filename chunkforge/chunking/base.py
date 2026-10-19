"""Shared immutable chunk contract and Unicode-safe token budgeting."""

from abc import ABC, abstractmethod
from dataclasses import dataclass

import tiktoken


@dataclass(frozen=True, slots=True)
class Chunk:
    """One searchable source span; offsets are Python characters, not UTF-8 bytes.

    Sentence-window chunks carry a separate surrounding context span. Late chunks
    carry the pooled vector and the passage that supplied its contextual tokens.
    """

    text: str
    doc_id: str
    chunk_index: int
    char_start: int
    char_end: int
    token_count: int
    strategy: str
    context_start: int | None = None
    context_end: int | None = None
    context_text: str | None = None
    embedding: tuple[float, ...] | None = None
    embedding_model: str | None = None


class TokenCounter:
    """Count reproducible tiktoken units without treating source text as commands."""

    def __init__(self, encoding_name: str = "cl100k_base") -> None:
        self.encoding = tiktoken.get_encoding(encoding_name)

    def count(self, text: str) -> int:
        """Count tokens, permitting literal special-token strings in documents."""
        return len(self.encoding.encode(text, disallowed_special=()))

    def prefix_end(self, text: str, limit: int) -> int:
        """Return a nonempty Unicode-safe prefix fitting a positive token budget."""
        if limit < 1:
            raise ValueError("Token budget must be positive")
        tokens = self.encoding.encode(text, disallowed_special=())
        if len(tokens) <= limit:
            return len(text)
        _, offsets = self.encoding.decode_with_offsets(tokens)
        end = offsets[limit]
        while end > 0 and self.count(text[:end]) > limit:
            end -= 1
        if end == 0:
            raise ValueError("Token budget cannot fit the first Unicode character")
        return end


class Chunker(ABC):
    """Strategy interface, analogous to a Java interface with one operation."""

    @abstractmethod
    def chunk(self, text: str, doc_id: str) -> list[Chunk]:
        """Split text into ordered source spans; return no chunks for blank input."""


def make_chunk(
    text: str,
    doc_id: str,
    index: int,
    start: int,
    end: int,
    counter: TokenCounter,
    strategy: str,
) -> Chunk:
    """Build metadata from the original source rather than reconstructed tokens."""
    return Chunk(
        text[start:end], doc_id, index, start, end, counter.count(text[start:end]), strategy
    )
