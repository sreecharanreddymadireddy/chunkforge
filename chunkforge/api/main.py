"""FastAPI application entry point.

Phase 0 exposes a real liveness endpoint so the package and multi-stage API image
are runnable now. Search and answer routes are added after their underlying
pipeline exists, preserving the rule that exposed behavior must be implemented.
"""

from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Stable response contract for the liveness endpoint."""

    status: Literal["ok"]
    service: Literal["chunkforge-api"]
    version: str


app = FastAPI(
    title="ChunkForge API",
    description="Production-style RAG chunking and retrieval workbench.",
    version="0.1.0",
)


@app.get("/health", response_model=HealthResponse, tags=["operations"])
def health() -> HealthResponse:
    """Report process liveness without requiring downstream dependencies."""

    return HealthResponse(status="ok", service="chunkforge-api", version=app.version)

