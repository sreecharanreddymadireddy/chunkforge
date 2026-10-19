"""Separate precise searchable sentence spans from surrounding answer context."""

from dataclasses import replace

from chunkforge.chunking.base import Chunk, Chunker, TokenCounter, make_chunk
from chunkforge.chunking.boundaries import sentence_spans
from chunkforge.chunking.recursive import RecursiveChunker


class SentenceWindowChunker(Chunker):
    """1. Detect sentence spans using the shared boundary rules.
    2. Choose each sentence as an independently searchable anchor.
    3. Split oversized anchors to enforce the core token limit.
    4. Attach the original sentence plus window_size neighbors on each side.
    5. Keep core and context offsets distinct for later retrieval expansion.
    """

    def __init__(
        self,
        window_size: int = 1,
        chunk_size: int = 512,
        counter: TokenCounter | None = None,
    ) -> None:
        if window_size < 0 or chunk_size < 1:
            raise ValueError("Require window_size >= 0 and chunk_size > 0")
        self.window_size = window_size
        self.chunk_size = chunk_size
        self.counter = counter or TokenCounter()

    def chunk(self, text: str, doc_id: str) -> list[Chunk]:
        """Return bounded anchors with potentially larger, explicitly separate contexts."""
        if not text.strip():
            return []
        spans = sentence_spans(text)
        splitter = RecursiveChunker(self.chunk_size, self.counter)
        chunks: list[Chunk] = []
        for i, (a, b) in enumerate(spans):
            left = spans[max(0, i - self.window_size)][0]
            right = spans[min(len(spans) - 1, i + self.window_size)][1]
            for core in splitter.chunk(text[a:b], doc_id):
                chunk = make_chunk(
                    text,
                    doc_id,
                    len(chunks),
                    a + core.char_start,
                    a + core.char_end,
                    self.counter,
                    "sentence_window",
                )
                chunks.append(
                    replace(
                        chunk,
                        context_start=left,
                        context_end=right,
                        context_text=text[left:right],
                    )
                )
        return chunks
