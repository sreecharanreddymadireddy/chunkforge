"""Validated search and grounded-answer routes with bounded inference capacity."""

import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from time import perf_counter
from typing import Annotated, Literal, cast

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

from chunkforge.api.service import Backend, BusyError, LocalBackend
from chunkforge.config import Settings
from chunkforge.generation.rag import Answer
from chunkforge.retrieval.fusion import RankedHit

Strategy = Literal["fixed", "recursive", "semantic", "sentence_window", "late"]


class QueryRequest(BaseModel):
    """An explicit HTTP contract; reject unsupported strategies and blank questions."""

    query: str = Field(min_length=1, max_length=2000)
    strategy: Strategy = "recursive"

    @field_validator("query")
    @classmethod
    def nonblank(cls, value: str) -> str:
        """Normalize surrounding whitespace without rewriting query meaning."""
        if not value.strip():
            raise ValueError("Query must not be blank")
        return value.strip()


class SearchResponse(BaseModel):
    """Expose final evidence and independent scores, not all intermediate candidate text."""

    query: str
    strategy: Strategy
    mode: str
    reasons: tuple[str, ...]
    hits: list[RankedHit]
    milliseconds: float


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Models load on first work request; health/docs remain cheap and available."""
    backend = LocalBackend()
    application.state.backend = backend
    try:
        yield
    finally:
        backend.close()


def get_backend(request: Request) -> Backend:
    """Allow dependency overrides in tests; normal requests share one managed runtime."""
    return cast(Backend, request.app.state.backend)


class HealthResponse(BaseModel):
    """Stable response contract for the liveness endpoint."""

    status: Literal["ok"]
    service: Literal["chunkforge-api"]
    version: str


app = FastAPI(
    title="ChunkForge API",
    description="Production-style RAG chunking and retrieval workbench.",
    version="0.1.0",
    lifespan=lifespan,
)
origins = json.loads(
    os.environ.get(
        "CHUNKFORGE_CORS_ORIGINS", json.dumps(Settings.model_fields["cors_origins"].default)
    )
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.get("/health", response_model=HealthResponse, tags=["operations"])
def health() -> HealthResponse:
    """Report process liveness without requiring downstream dependencies."""

    return HealthResponse(status="ok", service="chunkforge-api", version=app.version)


@app.post("/search", response_model=SearchResponse, tags=["retrieval"])
def search(
    payload: QueryRequest, backend: Annotated[Backend, Depends(get_backend)]
) -> SearchResponse:
    """Synchronous CPU work runs in FastAPI's thread pool, not its async event loop."""
    started = perf_counter()
    try:
        result = backend.search(payload.query, payload.strategy)
        return SearchResponse(
            query=payload.query,
            strategy=payload.strategy,
            mode=result.mode,
            reasons=result.reasons,
            hits=result.hits,
            milliseconds=(perf_counter() - started) * 1000,
        )
    except BusyError as exc:
        raise HTTPException(
            429, "Local inference busy; retry later", headers={"Retry-After": "2"}
        ) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except (OSError, RuntimeError, psycopg.OperationalError, psycopg.InterfaceError) as exc:
        raise HTTPException(503, "Retrieval dependencies unavailable") from exc


@app.post("/ask", response_model=Answer, tags=["generation"])
def ask(payload: QueryRequest, backend: Annotated[Backend, Depends(get_backend)]) -> Answer:
    """Return validated citations or explicit abstention/evidence-only degradation."""
    try:
        return backend.ask(payload.query, payload.strategy)
    except BusyError as exc:
        raise HTTPException(
            429, "Local inference busy; retry later", headers={"Retry-After": "2"}
        ) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except (OSError, RuntimeError, psycopg.OperationalError, psycopg.InterfaceError) as exc:
        raise HTTPException(503, "Retrieval dependencies unavailable") from exc
