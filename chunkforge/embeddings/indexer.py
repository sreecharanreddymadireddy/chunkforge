"""Reusable document-level indexing with the same fingerprints as the batch job."""

import hashlib
from dataclasses import dataclass
from time import perf_counter

from chunkforge.chunking.base import Chunker, TokenCounter
from chunkforge.chunking.fixed import FixedChunker
from chunkforge.chunking.late import LateChunker
from chunkforge.chunking.recursive import RecursiveChunker
from chunkforge.chunking.semantic import SemanticChunker
from chunkforge.chunking.sentence_window import SentenceWindowChunker
from chunkforge.config import Settings
from chunkforge.embeddings.embedder import Embedder, EmbeddingBackend
from chunkforge.store.pgvector_store import STRATEGIES, PgvectorStore


@dataclass(frozen=True)
class IndexOutcome:
    """Never claim recomputed row counts for a skipped completion marker."""

    strategy: str
    status: str
    chunks: int | None
    seconds: float


class DocumentIndexer:
    """Process one source across chosen strategies; replays skip committed unchanged work."""

    def __init__(
        self,
        settings: Settings,
        backend: EmbeddingBackend,
        embedder: Embedder,
        store: PgvectorStore,
    ) -> None:
        s = settings
        counter = TokenCounter(s.token_encoding)
        self.chunkers: dict[str, Chunker] = {
            "fixed": FixedChunker(s.chunk_size, s.chunk_overlap, counter),
            "recursive": RecursiveChunker(s.chunk_size, counter),
            "semantic": SemanticChunker(backend, s.chunk_size, s.semantic_threshold, counter),
            "sentence_window": SentenceWindowChunker(s.sentence_window_size, s.chunk_size, counter),
            "late": LateChunker(backend, s.late_chunk_size, counter),
        }
        self.config: dict[str, object] = {
            "pipeline_version": 1,
            "token_encoding": s.token_encoding,
            "chunk_size": s.chunk_size,
            "overlap": s.chunk_overlap,
            "semantic_threshold": s.semantic_threshold,
            "sentence_window": s.sentence_window_size,
            "late_size": s.late_chunk_size,
            "dimensions": s.embedding_dimensions,
        }
        self.embedder, self.store = embedder, store

    def index(self, text: str, doc_id: str, strategies: list[str]) -> list[IndexOutcome]:
        """Commit each document/strategy separately; callers commit Kafka only after all succeed."""
        if not text.strip() or not strategies or any(s not in STRATEGIES for s in strategies):
            raise ValueError("Require text and valid strategies")
        source_sha = hashlib.sha256(text.encode()).hexdigest()
        results = []
        for strategy in dict.fromkeys(strategies):
            started = perf_counter()
            fingerprint = self.store.fingerprint(
                source_sha, {**self.config, "strategy": strategy}, self.embedder.model_id
            )
            if self.store.has_document(doc_id, strategy, fingerprint):
                results.append(IndexOutcome(strategy, "unchanged", None, perf_counter() - started))
                continue
            records = self.embedder.embed_chunks(self.chunkers[strategy].chunk(text, doc_id))
            self.store.replace_document(doc_id, strategy, records, fingerprint)
            results.append(IndexOutcome(strategy, "stored", len(records), perf_counter() - started))
        return results
