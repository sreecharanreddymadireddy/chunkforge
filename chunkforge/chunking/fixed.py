"""Fixed-token sliding windows with explicit character-safe overlap."""

from chunkforge.chunking.base import Chunk, Chunker, TokenCounter, make_chunk


class FixedChunker(Chunker):
    """1. Count source tokens with the selected encoding.
    2. Choose the largest safe candidate prefix under the size budget.
    3. Store the original substring and its character offsets.
    4. Reuse a suffix of at most overlap tokens for the next window.
    5. Advance strictly and stop when the final source character is covered.
    """

    def __init__(
        self,
        chunk_size: int = 512,
        overlap: int = 50,
        counter: TokenCounter | None = None,
    ) -> None:
        if chunk_size < 1 or not 0 <= overlap < chunk_size:
            raise ValueError("Require chunk_size > 0 and 0 <= overlap < chunk_size")
        self.chunk_size = chunk_size
        self.overlap = overlap
        self.counter = counter or TokenCounter()

    def chunk(self, text: str, doc_id: str) -> list[Chunk]:
        """Produce bounded windows, preserving Unicode and original offsets."""
        if not text.strip():
            return []
        chunks: list[Chunk] = []
        start = 0
        while start < len(text):
            end = start + self.counter.prefix_end(text[start:], self.chunk_size)
            chunks.append(make_chunk(text, doc_id, len(chunks), start, end, self.counter, "fixed"))
            if end == len(text):
                break
            next_start = end
            if self.overlap:
                tokens = self.counter.encoding.encode(text[start:end], disallowed_special=())
                _, offsets = self.counter.encoding.decode_with_offsets(tokens)
                next_start = start + offsets[max(0, len(tokens) - self.overlap)]
                while next_start < end and self.counter.count(text[next_start:end]) > self.overlap:
                    next_start += 1
            start = max(start + 1, next_start)
        return chunks
