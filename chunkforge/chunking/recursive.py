"""Structure-first splitting with hard token limits and lossless source spans."""

import re

from chunkforge.chunking.base import Chunk, Chunker, TokenCounter, make_chunk
from chunkforge.chunking.boundaries import sentence_spans


class RecursiveChunker(Chunker):
    """1. Prefer blank-line paragraph boundaries.
    2. Split oversized paragraphs into sentence spans.
    3. Split oversized sentences using bounded token prefixes.
    4. Greedily pack adjacent units while the joined span fits.
    5. Emit contiguous original substrings without overlap or discarded whitespace.
    """

    def __init__(self, chunk_size: int = 512, counter: TokenCounter | None = None) -> None:
        if chunk_size < 1:
            raise ValueError("chunk_size must be positive")
        self.chunk_size = chunk_size
        self.counter = counter or TokenCounter()

    def _units(self, text: str) -> list[tuple[int, int]]:
        """Refine only spans that exceed the budget, preserving source order."""
        ends = [m.end() for m in re.finditer(r"\n\s*\n", text)] + [len(text)]
        units: list[tuple[int, int]] = []
        start = 0
        for end in ends:
            if end <= start:
                continue
            if self.counter.count(text[start:end]) <= self.chunk_size:
                units.append((start, end))
            else:
                for a, b in sentence_spans(text[start:end]):
                    pos, stop = start + a, start + b
                    while pos < stop:
                        next_pos = pos + self.counter.prefix_end(text[pos:stop], self.chunk_size)
                        units.append((pos, next_pos))
                        pos = next_pos
            start = end
        return units

    def chunk(self, text: str, doc_id: str) -> list[Chunk]:
        """Pack structural units into bounded, non-overlapping chunks."""
        if not text.strip():
            return []
        chunks: list[Chunk] = []
        start = 0
        end = 0
        for a, b in self._units(text):
            if end > start and self.counter.count(text[start:b]) > self.chunk_size:
                chunks.append(
                    make_chunk(
                        text,
                        doc_id,
                        len(chunks),
                        start,
                        end,
                        self.counter,
                        "recursive",
                    )
                )
                start = a
            end = b
        chunks.append(make_chunk(text, doc_id, len(chunks), start, end, self.counter, "recursive"))
        return chunks
