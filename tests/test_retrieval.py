"""Deterministic retrieval-stage contracts, ranking arithmetic and failure behavior."""

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from chunkforge.resilience.breaker import CircuitBreaker
from chunkforge.resilience.retry import RetryPolicy
from chunkforge.retrieval import demo
from chunkforge.retrieval.bm25 import BM25Index, tokenize
from chunkforge.retrieval.fusion import RankedHit, reciprocal_rank_fusion
from chunkforge.retrieval.pipeline import RetrievalPipeline
from chunkforge.retrieval.reranker import CrossEncoderScorer, Reranker
from chunkforge.store.pgvector_store import SearchResult


def evidence(index: int, text: str, score: float = 0.0) -> SearchResult:
    """Create source-aligned evidence with a stable identity."""
    return SearchResult("paper", index, text, 0, len(text), score, None)


class FixtureScorer:
    """Record exactly which candidates reach pairwise scoring."""

    def __init__(self) -> None:
        self.passages: list[str] = []

    def score(self, query: str, passages: list[str]) -> list[float]:
        """Prefer the identifier-bearing passage for an inspectable ordering."""
        self.passages = passages
        return [10.0 if "DivOPD" in text else -1.0 for text in passages]


def test_bm25_identifiers_oov_empty_and_deterministic_ties() -> None:
    """Keyword matches preserve exact identifiers; unseen terms return no fabricated hits."""
    assert tokenize("DIVOPD v1.5 AB-123") == ["divopd", "v1.5", "ab-123"]
    index = BM25Index(
        [
            evidence(0, "DivOPD selects turns"),
            evidence(1, "cats sleep"),
            evidence(2, "vectors store meaning"),
        ]
    )
    assert index.search("DivOPD")[0].chunk_index == 0
    assert index.search("unseenidentifier") == []
    assert BM25Index([]).search("query") == []
    assert BM25Index([evidence(0, "!!!")]).search("query") == []
    with pytest.raises(ValueError):
        index.search(" ")
    with pytest.raises(ValueError):
        BM25Index([evidence(0, "a"), evidence(0, "b")])


def test_rrf_math_duplicate_guard_and_score_separation() -> None:
    """A chunk present in both lists accumulates rank contributions, never raw scores."""
    a, b, c = evidence(0, "a", 0.9), evidence(1, "b", 0.8), evidence(2, "c", 20)
    ranked = reciprocal_rank_fusion([a, b], [replace(b, score=30), c])
    assert ranked[0].chunk.chunk_index == 1
    assert ranked[0].rrf_score == pytest.approx(1 / 62 + 1 / 61)
    assert ranked[0].dense_score == 0.8
    assert ranked[0].bm25_score == 30
    assert reciprocal_rank_fusion([a, a], [])[0].rrf_score == pytest.approx(1 / 61)
    assert reciprocal_rank_fusion([], []) == []
    with pytest.raises(ValueError):
        reciprocal_rank_fusion([], [], 0)


def test_pipeline_candidate_limits_reranking_and_shape() -> None:
    """Dense 50 + lexical 50 -> at most 20 pairwise evaluations -> 5 evidence hits."""
    chunks = [evidence(i, f"DivOPD passage {i}" if i == 19 else f"passage {i}") for i in range(60)]
    limits: list[int] = []

    def dense(query: str, strategy: str, limit: int) -> list[SearchResult]:
        limits.append(limit)
        return chunks[:limit]

    scorer = FixtureScorer()
    pipeline = RetrievalPipeline(dense, BM25Index(chunks), Reranker(scorer), "fixed")
    result = pipeline.search("DivOPD")
    assert limits == [50]
    assert len(scorer.passages) == 20
    assert len(result.hits) == 5
    assert result.mode == "hybrid"
    assert result.hits[0].chunk.chunk_index == 19
    assert result.hits[0].rerank_score == 10
    with pytest.raises(ValueError):
        pipeline.search(" ")
    with pytest.raises(ValueError):
        RetrievalPipeline(dense, BM25Index([]), Reranker(scorer), "unknown")


def test_dense_outage_falls_back_then_opens_breaker() -> None:
    """A mock encoder outage still returns lexical evidence without invoking the reranker."""
    calls: list[int] = []

    def unavailable(query: str, strategy: str, limit: int) -> list[SearchResult]:
        calls.append(1)
        raise OSError("embedding process unavailable")

    scorer = FixtureScorer()
    pipeline = RetrievalPipeline(
        unavailable,
        BM25Index([evidence(0, "DivOPD selects turns")]),
        Reranker(scorer),
        "recursive",
        retry=RetryPolicy(2, 0, 0),
        breaker=CircuitBreaker(1, 30),
    )
    response = pipeline.search("DivOPD")
    assert response.mode == "bm25_only" and response.hits
    assert response.hits[0].dense_score is None
    assert response.hits[0].rerank_score is None
    assert response.reasons == ("dense_unavailable",)
    assert len(calls) == 2 and not scorer.passages
    assert pipeline.search("DivOPD").reasons == ("dense_circuit_open",)
    assert len(calls) == 2


def test_reranker_failure_keeps_fusion_but_validation_is_not_swallowed() -> None:
    """Operational outages degrade honestly; malformed provider output fails visibly."""
    chunk = evidence(0, "evidence")

    def dense(query: str, strategy: str, limit: int) -> list[SearchResult]:
        return [chunk]

    class BrokenScorer:
        """Fail a model dependency rather than falsifying relevance scores."""

        def score(self, query: str, passages: list[str]) -> list[float]:
            raise OSError("model unavailable")

    pipeline = RetrievalPipeline(dense, BM25Index([chunk]), Reranker(BrokenScorer()), "fixed")
    assert pipeline.search("evidence").mode == "hybrid_unreranked"

    class InvalidScorer:
        """A wrong score count is a contract bug, not a retryable outage."""

        def score(self, query: str, passages: list[str]) -> list[float]:
            return []

    with pytest.raises(ValueError):
        Reranker(InvalidScorer()).rerank("q", [RankedHit(chunk)])
    assert Reranker(InvalidScorer()).rerank("q", []) == []


def test_cross_encoder_windowing_retains_tail_and_rejects_long_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise real window-control code with a character-tokenizer and no model download."""
    pairs_seen: list[tuple[str, str]] = []

    class Tokenizer:
        """One character per token makes every source boundary observable."""

        def __call__(self, text: str, **kwargs: Any) -> dict[str, object]:
            return {
                "input_ids": list(range(len(text))),
                "offset_mapping": [(i, i + 1) for i in range(len(text))],
            }

        def num_special_tokens_to_add(self, pair: bool) -> int:
            return 3

    class Model:
        """Return a larger logit for a passage window containing the tail marker."""

        tokenizer = Tokenizer()
        model = SimpleNamespace(config=SimpleNamespace(_commit_hash="revision"))

        def predict(self, pairs: list[tuple[str, str]], **kwargs: Any) -> list[float]:
            pairs_seen.extend(pairs)
            return [7.0 if "TAIL" in passage else -2.0 for _, passage in pairs]

    monkeypatch.setitem(
        __import__("sys").modules,
        "torch",
        SimpleNamespace(nn=SimpleNamespace(Identity=lambda: None)),
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "sentence_transformers",
        SimpleNamespace(CrossEncoder=lambda *args, **kwargs: Model()),
    )
    scorer = CrossEncoderScorer("fixture")
    passage = "x" * 1100 + "TAIL"
    assert scorer.score("q", [passage]) == [7.0]
    assert "".join(p for _, p in pairs_seen) == passage
    assert all(len(q) + len(p) + 3 <= 512 for q, p in pairs_seen)
    assert scorer.score("q", []) == []
    with pytest.raises(ValueError):
        scorer.score("x" * 129, ["p"])
    with pytest.raises(ValueError):
        CrossEncoderScorer("fixture", batch_size=0)


def test_demo_runs_trace_cache_and_controlled_outage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CLI wiring uses the same pipeline as the model run, without network in unit tests."""
    settings = SimpleNamespace(
        embedding_model="fixture",
        embedding_batch_size=2,
        embedding_device="cpu",
        embedding_dimensions=2,
        embedding_query_instruction="search: ",
        redis_url="redis://unused",
        redis_timeout_seconds=0.1,
        cache_ttl_seconds=60,
        postgres_dsn="unused",
        vector_table="chunkforge_fixture",
        hnsw_ef_search=100,
        reranker_model="fixture",
        retrieval_candidates=50,
        rerank_candidates=20,
        retrieval_limit=5,
        rrf_k=60,
        retry_attempts=1,
        retry_base_delay=0,
        retry_max_delay=0,
        breaker_failure_threshold=1,
        breaker_recovery_seconds=30,
    )
    chunk = evidence(0, "DivOPD evidence")

    class Backend:
        """Provide a versioned normal embedding and no unnecessary contextual path."""

        model_name = "fixture"
        transformer = SimpleNamespace(
            auto_model=SimpleNamespace(config=SimpleNamespace(_commit_hash="revision"))
        )

        def encode(self, texts: list[str]) -> list[tuple[float, ...]]:
            return [(1.0, 0.0) for _ in texts]

    class Store:
        """Keep dense and lexical snapshot identities aligned."""

        def lexical_snapshot(self, *args: object) -> list[SearchResult]:
            return [chunk]

        def search(self, *args: object, **kwargs: object) -> list[SearchResult]:
            assert kwargs["document_ids"] == ["paper"]
            return [chunk]

    class Client:
        """Retain cache entries across repeated queries within one command."""

        def __init__(self) -> None:
            self.values: dict[str, str] = {}

        def get(self, key: str) -> object:
            return self.values.get(key)

        def setex(self, name: str, time: int, value: str) -> object:
            self.values[name] = value
            return True

        def close(self) -> None:
            pass

    scorer = FixtureScorer()
    monkeypatch.setattr(demo, "get_settings", lambda: settings)
    monkeypatch.setattr(demo, "BGEBackend", lambda *args: Backend())
    monkeypatch.setattr(demo, "PgvectorStore", lambda *args: Store())
    monkeypatch.setattr(demo, "Redis", SimpleNamespace(from_url=lambda *args, **kwargs: Client()))
    monkeypatch.setattr(
        demo,
        "CrossEncoderScorer",
        lambda *args: SimpleNamespace(model_id="fixture@revision", score=scorer.score),
    )
    output = tmp_path / "trace.json"
    args = ["search", "--query", "DivOPD", "--repeat", "2", "--output", str(output)]
    monkeypatch.setattr("sys.argv", args)
    demo.main()
    report = json.loads(output.read_text())
    assert report["traces"][1]["cache_hits"] == 1
    assert report["traces"][0]["mode"] == "hybrid"
    monkeypatch.setattr("sys.argv", [*args, "--fail-dense"])
    demo.main()
    report = json.loads(output.read_text())
    assert report["traces"][1]["reasons"] == ["dense_circuit_open"]
    assert "snapshot_chunks" in capsys.readouterr().out
