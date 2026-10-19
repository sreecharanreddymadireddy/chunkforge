"""Batched BGE embeddings with explicit pooling identity and no silent truncation."""

import math
from dataclasses import dataclass
from typing import Protocol

from chunkforge.chunking.base import Chunk
from chunkforge.chunking.model_backend import ContextEncoder, SentenceEncoder, normalized_mean


class EmbeddingBackend(SentenceEncoder, ContextEncoder, Protocol):
    """Inject the model behind a small typed contract for deterministic tests."""


class VectorCache(Protocol):
    """Optional vector cache contract; keep the embedder independent of Redis imports."""

    def get(self, identity: str, text: str, dimensions: int) -> tuple[float, ...] | None:
        """Read a compatible vector or return a cache miss."""

    def put(self, identity: str, text: str, vector: tuple[float, ...]) -> None:
        """Cache a valid vector with an implementation-defined lifetime."""


@dataclass(frozen=True, slots=True)
class EmbeddedChunk:
    """Source chunk plus its immutable embedding-space identity."""

    chunk: Chunk
    vector: tuple[float, ...]
    model_id: str
    representation: str


def normalize_vector(vector: tuple[float, ...], dimensions: int) -> tuple[float, ...]:
    """Reject nonfinite, empty, zero or wrong-width vectors before persistence."""
    if len(vector) != dimensions or not all(math.isfinite(x) for x in vector):
        raise ValueError(f"Expected {dimensions} finite embedding coordinates")
    norm = math.sqrt(sum(x * x for x in vector))
    if not math.isfinite(norm) or norm == 0:
        raise ValueError("Embedding must have finite nonzero length")
    return tuple(x / norm for x in vector)


class Embedder:
    """Convert chunk cores to vectors; preserve late vectors and match query pooling.

    Standard cores exceeding the model tokenizer limit are encoded as bounded
    pieces and averaged by BGEBackend. This is an explicit approximation, not
    full-context encoding. Late vectors retain their original passage context.
    """

    def __init__(
        self,
        backend: EmbeddingBackend,
        model_id: str,
        dimensions: int = 384,
        batch_size: int = 32,
        query_instruction: str = "Represent this sentence for searching relevant passages: ",
        cache: VectorCache | None = None,
    ) -> None:
        if dimensions < 1 or batch_size < 1 or not model_id:
            raise ValueError("Require positive dimensions/batch_size and a model identity")
        self.backend = backend
        self.model_id = model_id
        self.dimensions = dimensions
        self.batch_size = batch_size
        self.query_instruction = query_instruction
        self.cache = cache

    @staticmethod
    def representation(strategy: str) -> str:
        """Separate standard BGE pooling from the experimental late-pooling space."""
        return "contextual_token_mean" if strategy == "late" else "bounded_piece_mean"

    def embed_chunks(self, chunks: list[Chunk]) -> list[EmbeddedChunk]:
        """Batch independent cores while retaining input order and late vectors."""
        vectors: dict[int, tuple[float, ...]] = {}
        pending: list[int] = []
        for i, chunk in enumerate(chunks):
            if not chunk.text.strip():
                raise ValueError("Cannot embed a blank searchable core")
            if chunk.strategy == "late":
                if chunk.embedding is None or chunk.embedding_model != self.backend.model_name:
                    raise ValueError("Late chunk must carry vectors from the configured model")
                vectors[i] = normalize_vector(chunk.embedding, self.dimensions)
            else:
                identity = self.model_id + ":document:" + self.representation(chunk.strategy)
                cached = (
                    self.cache.get(identity, chunk.text, self.dimensions) if self.cache else None
                )
                if cached is None:
                    pending.append(i)
                else:
                    vectors[i] = cached
        for start in range(0, len(pending), self.batch_size):
            indices = pending[start : start + self.batch_size]
            encoded = self.backend.encode([chunks[i].text for i in indices])
            if len(encoded) != len(indices):
                raise ValueError("Embedding provider returned an incorrect row count")
            for i, vector in zip(indices, encoded, strict=True):
                vectors[i] = normalize_vector(vector, self.dimensions)
                if self.cache:
                    identity = (
                        self.model_id + ":document:" + self.representation(chunks[i].strategy)
                    )
                    self.cache.put(identity, chunks[i].text, vectors[i])
        return [
            EmbeddedChunk(
                chunk,
                vectors[i],
                self.model_id,
                self.representation(chunk.strategy),
            )
            for i, chunk in enumerate(chunks)
        ]

    def embed_query(self, query: str, strategy: str) -> tuple[float, ...]:
        """Encode a nonblank query using the indexed strategy's pooling convention."""
        if not query.strip():
            raise ValueError("Query cannot be blank")
        instructed = self.query_instruction + query.strip()
        identity = self.model_id + ":query:" + self.representation(strategy)
        if self.cache:
            cached = self.cache.get(identity, instructed, self.dimensions)
            if cached is not None:
                return cached
        if strategy == "late":
            passages = self.backend.contextualize(instructed)
            vector = normalized_mean([v for passage in passages for v in passage.vectors])
        else:
            rows = self.backend.encode([instructed])
            if len(rows) != 1:
                raise ValueError("Query encoder must return exactly one vector")
            vector = rows[0]
        result = normalize_vector(vector, self.dimensions)
        if self.cache:
            self.cache.put(identity, instructed, result)
        return result
