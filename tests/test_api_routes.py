"""Typed HTTP routes, validation, CORS and resource-busy behavior without model loading."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from chunkforge.api.main import app, get_backend
from chunkforge.api.service import BusyError
from chunkforge.generation.rag import ABSTENTION, Answer
from chunkforge.retrieval.fusion import RankedHit
from chunkforge.retrieval.pipeline import RetrievalResponse
from chunkforge.store.pgvector_store import SearchResult


class FixtureBackend:
    """A deterministic service boundary; live model tests are separate measurements."""

    busy = False

    def search(self, query: str, strategy: str) -> RetrievalResponse:
        """Return one scored source core or explicit capacity rejection."""
        if self.busy:
            raise BusyError("occupied")
        hit = RankedHit(
            SearchResult("paper", 0, "Redis caches embeddings.", 0, 24, 0.8, None),
            dense_score=0.8,
            rrf_score=0.016,
            rerank_score=4.2,
        )
        return RetrievalResponse([hit], "hybrid", (), [], [], [])

    def ask(self, query: str, strategy: str) -> Answer:
        """Exercise the HTTP abstention schema without pretending to run inference."""
        return Answer("insufficient_evidence", ABSTENTION, [], [], "hybrid", (), "fixture")


@pytest.fixture
def client() -> Iterator[TestClient]:
    """Lifespan closes resources; overrides are always cleared for other tests."""
    backend = FixtureBackend()
    app.dependency_overrides[get_backend] = lambda: backend
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()


def test_search_ask_and_openapi_contracts(client: TestClient) -> None:
    """Scores, original evidence, answer status and endpoint schemas are public contracts."""
    r = client.post("/search", json={"query": " cache ", "strategy": "recursive"})
    assert r.status_code == 200
    assert r.json()["query"] == "cache"
    assert r.json()["hits"][0]["chunk"]["doc_id"] == "paper"
    assert r.json()["hits"][0]["bm25_score"] is None
    assert (
        client.post("/ask", json={"query": "unknown"}).json()["status"] == "insufficient_evidence"
    )
    assert client.get("/docs").status_code == 200
    assert {"/search", "/ask", "/health"}.issubset(client.get("/openapi.json").json()["paths"])


@pytest.mark.parametrize(
    "payload",
    [{"query": " "}, {"query": "q", "strategy": "not-a-strategy"}, {"query": "q" * 2001}, {}],
)
def test_invalid_input_returns_422(client: TestClient, payload: dict[str, str]) -> None:
    """Validation runs before expensive model work."""
    assert client.post("/search", json=payload).status_code == 422


def test_cors_and_busy_response(client: TestClient) -> None:
    """Allow only the configured browser origin; busy requests get a retry signal."""
    headers = {"Origin": "http://localhost:4200", "Access-Control-Request-Method": "POST"}
    assert client.options("/search", headers=headers).status_code == 200
    headers["Origin"] = "https://untrusted.example"
    assert client.options("/search", headers=headers).status_code == 400
    backend = FixtureBackend()
    backend.busy = True
    app.dependency_overrides[get_backend] = lambda: backend
    r = client.post("/search", json={"query": "q"})
    assert r.status_code == 429 and r.headers["retry-after"] == "2"
