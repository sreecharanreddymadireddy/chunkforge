"""Exercise report generation and input validation without model downloads in CI."""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from chunkforge.chunking import compare
from chunkforge.chunking.model_backend import ContextTokens


class ReportBackend:
    """Deterministic injected provider; never presented as measured BGE output."""

    def __init__(self, model_name: str, batch_size: int, device: str) -> None:
        self.model_name = model_name
        self.model = SimpleNamespace(max_seq_length=512)
        self.transformer = SimpleNamespace(
            auto_model=SimpleNamespace(
                config=SimpleNamespace(_commit_hash="test-revision"),
            )
        )

    def encode(self, texts: list[str]) -> list[tuple[float, ...]]:
        """Use identical directions so token budgets alone govern semantic splits."""
        return [(1.0, 0.0) for _ in texts]

    def contextualize(self, text: str) -> list[ContextTokens]:
        """Provide aligned contextual token fixtures for report-path validation."""
        return [
            ContextTokens(
                0,
                len(text),
                tuple((i, i + 1) for i in range(len(text))),
                tuple((1.0, 1.0) for _ in text),
            )
        ]


def test_report_contains_all_strategies_and_source_hashes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A runnable CLI validates full coverage and produces auditable report files."""
    text = "Vectors represent meaning. PostgreSQL stores vectors. " * 6
    (tmp_path / "paper.txt").write_text(text)
    output = tmp_path / "report.md"
    settings = SimpleNamespace(
        processed_data_dir=tmp_path,
        token_encoding="cl100k_base",
        embedding_model="test-model",
        embedding_batch_size=2,
        embedding_device="cpu",
        chunk_size=32,
        chunk_overlap=4,
        semantic_threshold=0.5,
        sentence_window_size=1,
        late_chunk_size=8,
        model_dump=lambda **kwargs: {},
    )
    monkeypatch.setattr(compare, "get_settings", lambda: settings)
    monkeypatch.setattr(compare, "BGEBackend", ReportBackend)
    monkeypatch.setattr("sys.argv", ["compare", "--documents", "1", "--output", str(output)])
    compare.main()
    report = json.loads(output.with_suffix(".json").read_text())
    assert {r["strategy"] for r in report["results"]} == {
        "fixed",
        "recursive",
        "semantic",
        "sentence_window",
        "late",
    }
    assert report["documents"][0]["sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert report["model_revision"] == "test-revision"
    assert "not retrieval-quality measurements" in output.read_text()
    monkeypatch.setattr("sys.argv", ["compare", "--documents", "2"])
    with pytest.raises(SystemExit, match="2"):
        compare.main()
    monkeypatch.setattr("sys.argv", ["compare", "--documents", "0"])
    with pytest.raises(SystemExit, match="2"):
        compare.main()
