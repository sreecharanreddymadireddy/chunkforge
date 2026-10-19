"""Sentence-similarity breakpoints with a hard token budget."""

from chunkforge.chunking.base import Chunk, Chunker, TokenCounter, make_chunk
from chunkforge.chunking.boundaries import sentence_spans
from chunkforge.chunking.model_backend import SentenceEncoder, normalized_mean
from chunkforge.chunking.recursive import RecursiveChunker


class SemanticChunker(Chunker):
    """1. Find sentence spans and split oversized sentences into safe units.
    2. Encode each unit using an injected sentence embedding model.
    3. Compare neighboring normalized vectors using their dot product.
    4. Break below the configured similarity threshold or at the token budget.
    5. Keep exact source spans; topic changes are heuristics, not ground truth.
    """

    def __init__(
        self,
        encoder: SentenceEncoder,
        chunk_size: int = 512,
        threshold: float = 0.5,
        counter: TokenCounter | None = None,
    ) -> None:
        if chunk_size < 1 or not -1 <= threshold <= 1:
            raise ValueError("Require positive chunk_size and threshold in [-1, 1]")
        self.encoder = encoder
        self.chunk_size = chunk_size
        self.threshold = threshold
        self.counter = counter or TokenCounter()

    def chunk(self, text: str, doc_id: str) -> list[Chunk]:
        """Split at adjacent topic shifts without dropping oversized sentences."""
        if not text.strip():
            return []
        units: list[tuple[int, int]] = []
        splitter = RecursiveChunker(self.chunk_size, self.counter)
        for a, b in sentence_spans(text):
            units.extend(
                (a + c.char_start, a + c.char_end) for c in splitter.chunk(text[a:b], doc_id)
            )
        rows = self.encoder.encode([text[a:b] for a, b in units])
        if len(rows) != len(units):
            raise ValueError("Encoder must return one vector per unit")
        vectors = [normalized_mean([row]) for row in rows]
        chunks: list[Chunk] = []
        start, end = units[0]
        for i in range(1, len(units)):
            a, b = units[i]
            if len(vectors[i - 1]) != len(vectors[i]):
                raise ValueError("Embedding dimensions differ")
            similarity = sum(x * y for x, y in zip(vectors[i - 1], vectors[i], strict=True))
            if similarity < self.threshold or self.counter.count(text[start:b]) > self.chunk_size:
                chunks.append(
                    make_chunk(
                        text,
                        doc_id,
                        len(chunks),
                        start,
                        end,
                        self.counter,
                        "semantic",
                    )
                )
                start = a
            end = b
        chunks.append(make_chunk(text, doc_id, len(chunks), start, end, self.counter, "semantic"))
        return chunks
