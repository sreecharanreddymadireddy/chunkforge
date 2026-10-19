"""Late mean pooling inside explicit model-sized contextual passages."""

from dataclasses import replace

from chunkforge.chunking.base import Chunk, Chunker, TokenCounter, make_chunk
from chunkforge.chunking.fixed import FixedChunker
from chunkforge.chunking.model_backend import ContextEncoder, normalized_mean


class LateChunker(Chunker):
    """1. Encode all tokens jointly inside each model-sized passage.
    2. Retain contextual token vectors and source character offsets.
    3. Split each encoded passage into smaller non-overlapping text chunks.
    4. Average vectors whose token spans intersect each chunk, then normalize.
    5. Store the pooled vector and its context range; never re-embed independently.
    """

    def __init__(
        self,
        encoder: ContextEncoder,
        chunk_size: int = 128,
        counter: TokenCounter | None = None,
    ) -> None:
        if chunk_size < 1:
            raise ValueError("chunk_size must be positive")
        self.encoder = encoder
        self.chunk_size = chunk_size
        self.counter = counter or TokenCounter()

    def chunk(self, text: str, doc_id: str) -> list[Chunk]:
        """Retain real contextual vectors; BGE provides passage-local context only."""
        if not text.strip():
            return []
        chunks: list[Chunk] = []
        expected_start = 0
        for passage in self.encoder.contextualize(text):
            if passage.start != expected_start or not passage.start < passage.end <= len(text):
                raise ValueError("Context passages must cover source contiguously")
            if len(passage.offsets) != len(passage.vectors):
                raise ValueError("Context token offsets and vectors must align")
            splitter = FixedChunker(self.chunk_size, overlap=0, counter=self.counter)
            for core in splitter.chunk(text[passage.start : passage.end], doc_id):
                a, b = passage.start + core.char_start, passage.start + core.char_end
                selected = [
                    vector
                    for (start, end), vector in zip(
                        passage.offsets,
                        passage.vectors,
                        strict=True,
                    )
                    if start < b and end > a
                ]
                # Whitespace has tiktoken units but may have no embedding-model token.
                if not selected and chunks and not text[a:b].strip():
                    previous = chunks[-1]
                    combined_count = self.counter.count(text[previous.char_start : b])
                    if combined_count <= self.chunk_size:
                        chunks[-1] = replace(
                            previous,
                            text=text[previous.char_start : b],
                            char_end=b,
                            token_count=combined_count,
                        )
                        continue
                    candidates = [
                        start for start, end in passage.offsets if previous.char_start < start < a
                    ]
                    if not candidates:
                        raise ValueError("Cannot fit whitespace tail with a contextual token")
                    a = candidates[-1]
                    previous_vectors = [
                        v
                        for (start, end), v in zip(
                            passage.offsets,
                            passage.vectors,
                            strict=True,
                        )
                        if start < a and end > previous.char_start
                    ]
                    previous_count = self.counter.count(text[previous.char_start : a])
                    if max(previous_count, self.counter.count(text[a:b])) > self.chunk_size:
                        raise ValueError("Whitespace rebalance exceeds token budget")
                    chunks[-1] = replace(
                        previous,
                        text=text[previous.char_start : a],
                        char_end=a,
                        token_count=previous_count,
                        embedding=normalized_mean(previous_vectors),
                    )
                    selected = [
                        v
                        for (start, end), v in zip(
                            passage.offsets,
                            passage.vectors,
                            strict=True,
                        )
                        if start < b and end > a
                    ]
                chunk = make_chunk(text, doc_id, len(chunks), a, b, self.counter, "late")
                chunks.append(
                    replace(
                        chunk,
                        embedding=normalized_mean(selected),
                        embedding_model=self.encoder.model_name,
                        context_start=passage.start,
                        context_end=passage.end,
                    )
                )
            expected_start = passage.end
        if expected_start != len(text):
            raise ValueError("Context encoder did not cover the full source")
        return chunks
