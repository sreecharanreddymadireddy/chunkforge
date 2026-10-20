"""Runtime wiring, snapshot refresh, bounded capacity and lifecycle without heavy models."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx
import psycopg
import pytest
from pydantic import AnyHttpUrl, PostgresDsn, RedisDsn

from chunkforge.api import service as api_service
from chunkforge.chunking.model_backend import ContextTokens
from chunkforge.config import Settings
from chunkforge.retrieval import runtime as runtime_module
from chunkforge.retrieval.runtime import SearchRuntime
from chunkforge.store.pgvector_store import SearchResult


@pytest.fixture
def runtime(monkeypatch: pytest.MonkeyPatch) -> SearchRuntime:
    """Inject only external providers; exercise actual embedding, cache and pipeline composition."""

    class Backend:
        model_name = "fixture"
        transformer = SimpleNamespace(
            auto_model=SimpleNamespace(config=SimpleNamespace(_commit_hash="revision"))
        )

        def encode(self, texts: list[str]) -> list[tuple[float, ...]]:
            return [(1.0, 0.0) for _ in texts]

        def contextualize(self, text: str) -> list[ContextTokens]:
            return [ContextTokens(0, len(text), ((0, len(text)),), ((1.0, 0.0),))]

    class Client:
        closed = False

        def get(self, key: str) -> object:
            return None

        def setex(self, name: str, time: int, value: str) -> object:
            return True

        def close(self) -> None:
            self.closed = True

    class Store:
        version = "one"
        offline = False
        hit = SearchResult(
            "paper", 0, "Redis caches computed vectors, not generated answers.", 0, 53, 0.8, None
        )

        def lexical_snapshot(self, *args: object) -> list[SearchResult]:
            return [self.hit]

        def search(self, *args: object) -> list[SearchResult]:
            assert args[-1] == ["paper"]
            return [self.hit]

        def manifest_version(self, strategy: str) -> str:
            if self.offline:
                raise psycopg.OperationalError("offline")
            return self.version

    class Scorer:
        def score(self, query: str, passages: list[str]) -> list[float]:
            return [5.0 for _ in passages]

    client = Client()
    monkeypatch.setattr(runtime_module, "BGEBackend", lambda *args: Backend())
    monkeypatch.setattr(
        runtime_module, "Redis", SimpleNamespace(from_url=lambda *args, **kw: client)
    )
    monkeypatch.setattr(runtime_module, "PgvectorStore", lambda *args: Store())
    monkeypatch.setattr(runtime_module, "CrossEncoderScorer", lambda *args: Scorer())
    settings = Settings.model_construct(
        embedding_model="fixture",
        embedding_dimensions=2,
        redis_url=RedisDsn("redis://localhost:6379/15"),
        postgres_dsn=PostgresDsn("postgresql://test:test@localhost/test"),
        reranker_model="fixture",
        ollama_base_url=AnyHttpUrl("http://ollama.test"),
        ollama_model="fixture",
        raw_data_dir=Path("unused"),
        processed_data_dir=Path("unused"),
    )
    result = SearchRuntime.create(settings)
    monkeypatch.setattr(api_service, "get_settings", lambda: settings)
    return result


def test_runtime_pipeline_filtering_and_client_lifecycle(runtime: SearchRuntime) -> None:
    """Model-compatible snapshots produce evidence and reject an empty document selection."""
    result = runtime.pipeline("recursive").search("Redis vectors")
    assert result.mode == "hybrid" and result.hits[0].rerank_score == 5
    with pytest.raises(ValueError):
        runtime.pipeline("recursive", [])
    runtime.close()
    assert cast(Any, runtime.client).closed


def test_api_refresh_preserves_breaker_and_warm_snapshot_on_db_failure(
    runtime: SearchRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ingestion refresh must not erase dependency failure state or remove warm fallback."""
    monkeypatch.setattr(SearchRuntime, "create", lambda settings: runtime)
    backend = api_service.LocalBackend()
    assert backend.search("Redis", "recursive").hits
    original = backend.snapshots["recursive"].pipeline.breaker
    cast(Any, runtime.store).version = "two"
    backend.search("Redis", "recursive")
    assert backend.snapshots["recursive"].pipeline.breaker is original
    cast(Any, runtime.store).offline = True
    assert backend.search("Redis", "recursive").hits
    assert backend.capacity.acquire(blocking=False)
    try:
        with pytest.raises(api_service.BusyError):
            backend.search("Redis", "recursive")
        with pytest.raises(api_service.BusyError):
            backend.ask("Redis", "recursive")
    finally:
        backend.capacity.release()
    backend.close()


def test_api_service_generation_uses_shared_snapshot_and_structural_citations(
    runtime: SearchRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The service invokes actual prompt construction/validation around a fixture HTTP reply."""
    monkeypatch.setattr(SearchRuntime, "create", lambda settings: runtime)
    backend = api_service.LocalBackend()
    with httpx.Client(
        base_url="http://ollama.test",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "done": True,
                    "message": {
                        "role": "assistant",
                        "content": (
                            '{"answer":"Redis caches vectors.",'
                            '"insufficient_evidence":false,"citation_ids":[1]}'
                        ),
                    },
                },
            )
        ),
    ) as client:
        backend.client = client
        answer = backend.ask("What does Redis cache?", "recursive")
        assert answer.status == "answered" and answer.answer is not None
        assert answer.answer.endswith("[1]")
        assert answer.citation_format == "application_suffix"
    backend.close()
