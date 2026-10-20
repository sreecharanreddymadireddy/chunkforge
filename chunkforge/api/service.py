"""Lazy, bounded local runtime lifecycle behind an injectable API service contract."""

from dataclasses import dataclass
from threading import BoundedSemaphore
from typing import Protocol

import httpx
import psycopg

from chunkforge.config import Settings, get_settings
from chunkforge.generation.rag import Answer, RagService, Snapshot
from chunkforge.retrieval.pipeline import RetrievalPipeline, RetrievalResponse
from chunkforge.retrieval.runtime import SearchRuntime


class BusyError(RuntimeError):
    """Local inference capacity is occupied; callers should retry later."""


class Backend(Protocol):
    """Typed seam for HTTP tests without model downloads or network dependencies."""

    def search(self, query: str, strategy: str) -> RetrievalResponse:
        """Return evidence from the current corpus snapshot."""

    def ask(self, query: str, strategy: str) -> Answer:
        """Return grounded output, abstention or explicitly degraded evidence."""


@dataclass
class PipelineSnapshot:
    """Keep a corpus fingerprint with the process-local BM25 index."""

    fingerprint: str
    pipeline: RetrievalPipeline


class LocalBackend:
    """Load once, refresh snapshots after committed ingestion, serialize CPU-heavy work."""

    def __init__(self) -> None:
        self.runtime: SearchRuntime | None = None
        self.settings: Settings | None = None
        self.client: httpx.Client | None = None
        self.snapshots: dict[str, PipelineSnapshot] = {}
        self.capacity = BoundedSemaphore(1)

    def _pipeline(self, strategy: str) -> RetrievalPipeline:
        """Rebuild lexical state on manifest changes; retain warm fallback on DB outage."""
        if self.runtime is None:
            self.settings = get_settings()
            self.runtime = SearchRuntime.create(self.settings)
        existing = self.snapshots.get(strategy)
        try:
            fingerprint = self.runtime.store.manifest_version(strategy)
        except (psycopg.OperationalError, psycopg.InterfaceError):
            if existing:
                return existing.pipeline
            raise
        if existing is None or existing.fingerprint != fingerprint:
            refreshed = self.runtime.pipeline(strategy)
            if existing is not None:
                refreshed.breaker = existing.pipeline.breaker
            existing = PipelineSnapshot(fingerprint, refreshed)
            self.snapshots[strategy] = existing
        return existing.pipeline

    def search(self, query: str, strategy: str) -> RetrievalResponse:
        """Reject concurrent heavy work rather than multiplying model memory use."""
        if not self.capacity.acquire(blocking=False):
            raise BusyError("Local inference is busy")
        try:
            return self._pipeline(strategy).search(query)
        finally:
            self.capacity.release()

    def ask(self, query: str, strategy: str) -> Answer:
        """Retrieval and generation share the same bounded resource slot."""
        if not self.capacity.acquire(blocking=False):
            raise BusyError("Local inference is busy")
        try:
            pipeline = self._pipeline(strategy)
            s = self.settings
            assert s is not None
            if self.client is None:
                self.client = httpx.Client(
                    base_url=str(s.ollama_base_url),
                    trust_env=False,
                    timeout=httpx.Timeout(s.generation_timeout_seconds, connect=3),
                )
            service = RagService(
                Snapshot(pipeline.search(query)),
                self.client,
                s.ollama_model,
                s.generation_context_size,
                s.generation_output_tokens,
                s.generation_template_reserve,
                s.generation_source_bytes,
                s.generation_temperature,
                s.generation_threads,
            )
            return service.ask(query)
        finally:
            self.capacity.release()

    def close(self) -> None:
        """Release HTTP/Redis clients when the application exits."""
        if self.client:
            self.client.close()
        if self.runtime:
            self.runtime.close()
