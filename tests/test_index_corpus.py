"""Test CLI orchestration, resumability and measured-output boundaries without model downloads."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from chunkforge.chunking.model_backend import ContextTokens
from chunkforge.embeddings import index_corpus
from chunkforge.embeddings.embedder import EmbeddedChunk
from chunkforge.store.pgvector_store import PgvectorStore, SearchResult


class IndexBackend:
    """Small deterministic provider exercising all strategy orchestration paths."""

    def __init__(self, model_name: str, batch_size: int, device: str) -> None:
        self.model_name = model_name
        self.transformer = SimpleNamespace(
            auto_model=SimpleNamespace(
                config=SimpleNamespace(_commit_hash="test-revision"),
            )
        )

    def encode(self, texts: list[str]) -> list[tuple[float, ...]]:
        """Return normalized sentence vectors without inference."""
        return [(1.0, 0.0) for _ in texts]

    def contextualize(self, text: str) -> list[ContextTokens]:
        """Supply aligned token vectors with full source coverage."""
        return [
            ContextTokens(
                0,
                len(text),
                tuple((i, i + 1) for i in range(len(text))),
                tuple((1.0, 0.0) for _ in text),
            )
        ]


class MemoryStore:
    """Observable storage fixture; SQL and rollback are tested against real pgvector separately."""

    fingerprint = staticmethod(PgvectorStore.fingerprint)

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], list[EmbeddedChunk]] = {}
        self.runs: dict[tuple[str, str], str] = {}
        self.created = False

    def create_schema(self) -> None:
        """Record initialization so writes can enforce the expected lifecycle."""
        self.created = True

    def has_document(self, doc_id: str, strategy: str, fingerprint: str) -> bool:
        """Skip only a previously completed matching run."""
        return self.runs.get((doc_id, strategy)) == fingerprint

    def replace_document(
        self,
        doc_id: str,
        strategy: str,
        records: list[EmbeddedChunk],
        fingerprint: str,
    ) -> None:
        """Keep rows and completion identity together for resume checks."""
        assert self.created
        self.rows[doc_id, strategy] = records
        self.runs[doc_id, strategy] = fingerprint

    def counts(self) -> dict[str, int]:
        """Aggregate stored rows by strategy."""
        return {key[1]: len(rows) for key, rows in self.rows.items()}

    def search(
        self,
        vector: tuple[float, ...],
        strategy: str,
        model_id: str,
        representation: str,
        limit: int = 5,
    ) -> list[SearchResult]:
        """Return matching fixture evidence without pretending it is measured model quality."""
        candidates = [r for (_, s), rows in self.rows.items() for r in rows if s == strategy]
        return [
            SearchResult(
                r.chunk.doc_id,
                r.chunk.chunk_index,
                r.chunk.text,
                r.chunk.char_start,
                r.chunk.char_end,
                1.0,
                r.chunk.context_text,
            )
            for r in candidates[:limit]
        ]


def test_index_cli_indexes_resumes_and_searches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Every strategy persists once, an unchanged rerun skips, and search emits latency metadata."""
    (tmp_path / "paper.txt").write_text("Vector search finds evidence. PostgreSQL stores vectors.")
    settings = SimpleNamespace(
        processed_data_dir=tmp_path,
        token_encoding="cl100k_base",
        embedding_model="test-model",
        embedding_batch_size=2,
        embedding_device="cpu",
        embedding_dimensions=2,
        embedding_query_instruction="search: ",
        postgres_dsn="test-dsn",
        vector_table="chunkforge_test",
        hnsw_ef_search=100,
        chunk_size=32,
        chunk_overlap=4,
        semantic_threshold=0.5,
        sentence_window_size=1,
        late_chunk_size=8,
    )
    store = MemoryStore()
    monkeypatch.setattr(index_corpus, "get_settings", lambda: settings)
    monkeypatch.setattr(index_corpus, "BGEBackend", IndexBackend)
    monkeypatch.setattr(index_corpus, "PgvectorStore", lambda *args: store)
    arguments = ["index", "--max-documents", "1", "--query", "vectors", "--repetitions", "2"]
    monkeypatch.setattr("sys.argv", arguments)
    index_corpus.main()
    assert len(store.rows) == 5
    assert "p50_search_ms" in capsys.readouterr().out
    index_corpus.main()
    assert capsys.readouterr().out.count("unchanged complete run") == 5
    monkeypatch.setattr("sys.argv", ["index", "--search-only", "--query", "vectors"])
    index_corpus.main()
    assert "Stored chunks" in capsys.readouterr().out
    for args in [["--max-documents", "0"], ["--search-only"], ["--max-documents", "2"]]:
        monkeypatch.setattr("sys.argv", ["index", *args])
        with pytest.raises(SystemExit):
            index_corpus.main()
