"""Test batching, numeric validation, query instructions and late-vector preservation."""

from dataclasses import replace

import pytest

from chunkforge.chunking.base import Chunk
from chunkforge.chunking.model_backend import ContextTokens
from chunkforge.embeddings.embedder import Embedder, normalize_vector
from chunkforge.store.pgvector_store import PgvectorStore


class SmallBackend:
    """Known two-dimensional provider; no model weights are required for unit tests."""

    model_name = "test-model"

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def encode(self, texts: list[str]) -> list[tuple[float, ...]]:
        """Track batch boundaries and return a vector with known normalized value."""
        self.calls.append(texts)
        return [(3.0, 4.0) for _ in texts]

    def contextualize(self, text: str) -> list[ContextTokens]:
        """Return a distinct direction for mean-pooled queries."""
        return [ContextTokens(0, len(text), ((0, len(text)),), ((0.0, 2.0),))]


def chunk(index: int = 0, strategy: str = "fixed") -> Chunk:
    """Create valid core metadata for embedding-boundary tests."""
    return Chunk("source", "doc", index, 0, 6, 1, strategy)


def test_batches_normalize_and_preserve_late_vectors() -> None:
    """Only ordinary cores enter inference; output order matches input order."""
    backend = SmallBackend()
    embedder = Embedder(backend, "test-model@revision", 2, 2)
    late = replace(chunk(1, "late"), embedding=(2.0, 0.0), embedding_model="test-model")
    result = embedder.embed_chunks([chunk(), late, chunk(2), chunk(3)])
    assert [len(call) for call in backend.calls] == [2, 1]
    assert result[0].vector == pytest.approx((0.6, 0.8))
    assert result[1].vector == (1.0, 0.0)
    assert result[1].representation == "contextual_token_mean"
    assert result[2].chunk.chunk_index == 2
    assert embedder.embed_chunks([]) == []


def test_queries_use_instructions_and_matching_pooling() -> None:
    """Use query instructions only at query time and match the late pooling space."""
    backend = SmallBackend()
    embedder = Embedder(backend, "test-model@revision", 2, query_instruction="search: ")
    assert embedder.embed_query("  question  ", "fixed") == pytest.approx((0.6, 0.8))
    assert backend.calls == [["search: question"]]
    assert embedder.embed_query("question", "late") == (0.0, 1.0)
    with pytest.raises(ValueError, match="blank"):
        embedder.embed_query(" ", "fixed")
    with pytest.raises(ValueError, match="Late chunk"):
        embedder.embed_chunks([chunk(strategy="late")])
    with pytest.raises(ValueError, match="blank"):
        embedder.embed_chunks([replace(chunk(), text=" ")])


@pytest.mark.parametrize("vector", [(0.0, 0.0), (1.0,), (float("nan"), 1.0), (float("inf"), 1.0)])
def test_reject_invalid_vectors(vector: tuple[float, ...]) -> None:
    """Invalid floating-point data cannot reach the database adapter."""
    with pytest.raises(ValueError):
        normalize_vector(vector, 2)


def test_store_validates_arguments_before_connecting() -> None:
    """Reject unsafe identifiers and invalid records without requiring PostgreSQL."""
    with pytest.raises(ValueError):
        PgvectorStore("unused", table_name="unrelated_table")
    with pytest.raises(ValueError):
        PgvectorStore("unused", dimensions=0)
    store = PgvectorStore("unused", dimensions=2)
    with pytest.raises(ValueError):
        store.search((1.0, 0.0), "unknown", "m", "p")
    with pytest.raises(ValueError):
        store.search((1.0, 0.0), "fixed", "m", "p", limit=0)
    with pytest.raises(ValueError):
        store.replace_document("doc", "fixed", [], "fingerprint")
    records = Embedder(SmallBackend(), "m", 2).embed_chunks([chunk()])
    with pytest.raises(ValueError, match="identity"):
        store.replace_document("another-document", "fixed", records, "fingerprint")
    config: dict[str, object] = {"size": 512}
    assert store.fingerprint("sha", config, "m") != store.fingerprint("changed", config, "m")
    assert store.fingerprint("sha", config, "m") != store.fingerprint("sha", config, "new-model")
