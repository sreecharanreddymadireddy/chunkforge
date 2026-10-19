"""Versioned Redis embedding cache; Redis loss must not prevent model inference."""

import hashlib
import json
import logging
from collections.abc import Callable
from typing import Protocol

from redis.exceptions import RedisError

from chunkforge.embeddings.embedder import normalize_vector

logger = logging.getLogger(__name__)


class CacheClient(Protocol):
    """Minimal Redis operations, suitable for an injected in-memory test implementation."""

    def get(self, key: str) -> object:
        """Return a serialized entry or None."""

    def setex(self, name: str, time: int, value: str) -> object:
        """Atomically write an entry with a lifetime in seconds."""


class EmbeddingCache:
    """Cache vectors, not answers; key includes all embedding-space and input identity.

    Text is hashed in Redis keys, but vectors are still sensitive derived data.
    No query text, credentials or raw keys are logged. Redis errors fail open.
    TTL bounds old entries; model/config changes invalidate through key identity.
    """

    def __init__(self, client: CacheClient, ttl_seconds: int = 86400) -> None:
        if ttl_seconds < 1:
            raise ValueError("Cache lifetime must be positive")
        self.client = client
        self.ttl_seconds = ttl_seconds
        self.hits = 0
        self.misses = 0
        self.errors = 0

    @staticmethod
    def key(identity: str, text: str, dimensions: int) -> str:
        """Hash unambiguous structured input, not concatenated ambiguous strings."""
        payload = json.dumps([1, identity, dimensions, text], ensure_ascii=False)
        return "chunkforge:embedding:v1:" + hashlib.sha256(payload.encode()).hexdigest()

    def get(self, identity: str, text: str, dimensions: int) -> tuple[float, ...] | None:
        """Treat corrupt entries as misses and validate width, finiteness and norm."""
        try:
            raw = self.client.get(self.key(identity, text, dimensions))
        except RedisError:
            self.errors += 1
            logger.warning("Embedding cache read unavailable")
            raw = None
        if isinstance(raw, (str, bytes)):
            try:
                data = json.loads(raw)
                if not isinstance(data, list) or any(type(x) not in (int, float) for x in data):
                    raise ValueError("Invalid cache vector payload")
                vector = normalize_vector(tuple(float(x) for x in data), dimensions)
            except (ValueError, TypeError, OverflowError):
                self.errors += 1
            else:
                self.hits += 1
                return vector
        self.misses += 1
        return None

    def put(self, identity: str, text: str, vector: tuple[float, ...]) -> None:
        """Write only valid vectors; malformed provider output is not a Redis failure."""
        vector = normalize_vector(vector, len(vector))
        try:
            self.client.setex(
                self.key(identity, text, len(vector)),
                self.ttl_seconds,
                json.dumps(vector, allow_nan=False),
            )
        except RedisError:
            self.errors += 1
            logger.warning("Embedding cache write unavailable")

    def get_or_compute(
        self,
        identity: str,
        text: str,
        dimensions: int,
        compute: Callable[[], tuple[float, ...]],
    ) -> tuple[float, ...]:
        """Avoid inference on a hit; compute outside Redis on a miss or cache outage."""
        cached = self.get(identity, text, dimensions)
        if cached is not None:
            return cached
        value = normalize_vector(compute(), dimensions)
        self.put(identity, text, value)
        return value
