"""Evaluation CLI bookkeeping and homework guard; fixture measurements stay in temporary files."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from chunkforge.eval import run_eval
from chunkforge.retrieval.fusion import RankedHit
from chunkforge.retrieval.pipeline import RetrievalResponse
from chunkforge.retrieval.runtime import SearchRuntime
from chunkforge.store.pgvector_store import SearchResult


def test_pilot_runner_shared_corpus_trace_and_homework_guard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every strategy uses the same sources; final mode rejects missing user-authored labels."""
    text = "An exact passage explaining why cached embeddings reduce repeated model work."
    (tmp_path / "paper.txt").write_text(text)
    pairs = tmp_path / "pairs.json"
    pairs.write_text(
        json.dumps(
            [
                {
                    "id": "one",
                    "source": "seed",
                    "category": "factual",
                    "question": "What is cached?",
                    "evidence": [{"doc_id": "paper", "text": text}],
                }
            ]
        )
    )
    hit = SearchResult("paper", 0, text, 0, len(text), 0.8, None)

    class Pipeline:
        def search(self, query: str) -> RetrievalResponse:
            return RetrievalResponse([RankedHit(hit)], "hybrid", (), [], [], [])

    class Store:
        def lexical_snapshot(self, *args: object) -> list[SearchResult]:
            return [hit]

    class Runtime:
        embedder = SimpleNamespace(model_id="fixture", representation=lambda strategy: "standard")
        store = Store()

        def pipeline(self, strategy: str, docs: list[str]) -> Pipeline:
            assert docs == ["paper"]
            return Pipeline()

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(
        run_eval, "get_settings", lambda: SimpleNamespace(processed_data_dir=tmp_path)
    )
    monkeypatch.setattr(SearchRuntime, "create", lambda s: Runtime())
    report = tmp_path / "RESULTS.md"
    args = ["eval", "--pairs", str(pairs), "--documents", "1", "--output", str(report)]
    monkeypatch.setattr("sys.argv", args)
    with pytest.raises(SystemExit):
        run_eval.main()
    monkeypatch.setattr("sys.argv", [*args, "--pilot"])
    run_eval.main()
    output = json.loads(report.with_suffix(".json").read_text())
    assert output["pilot"] and output["user_questions"] == 0
    assert len(output["measurements"]) == 5
    assert all(row["recall_at_5"] == 1.0 for row in output["measurements"])
