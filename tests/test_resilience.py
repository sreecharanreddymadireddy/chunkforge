"""Fake-clock resilience contracts plus opt-in real Redis latency and TTL checks."""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import perf_counter, sleep
from typing import cast
from uuid import uuid4

import pytest
from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

from chunkforge.chunking.base import Chunk
from chunkforge.chunking.model_backend import ContextTokens
from chunkforge.embeddings.embedder import Embedder
from chunkforge.resilience.breaker import CircuitBreaker, CircuitOpen
from chunkforge.resilience.cache import CacheClient, EmbeddingCache
from chunkforge.resilience.retry import RetryPolicy


class CacheBackend:
    """Count inference calls so cache tests prove avoided work, not just equal outputs."""

    model_name = "fixture"

    def __init__(self) -> None:
        self.calls = 0

    def encode(self, texts: list[str]) -> list[tuple[float, ...]]:
        """Return finite two-dimensional fixture vectors."""
        self.calls += 1
        return [(1.0, 0.0) for _ in texts]

    def contextualize(self, text: str) -> list[ContextTokens]:
        """Return one aligned contextual token for the cache-key separation test."""
        self.calls += 1
        return [ContextTokens(0, len(text), ((0, len(text)),), ((0.0, 1.0),))]


class MemoryRedis:
    """Small observable cache fake, independent of the real Redis integration check."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: list[int] = []
        self.down = False

    def get(self, key: str) -> object:
        """Raise a realistic client failure when the dependency is disabled."""
        if self.down:
            raise RedisConnectionError("offline")
        return self.values.get(key)

    def setex(self, name: str, time: int, value: str) -> object:
        """Record TTL and serialized payload, without pretending to implement a clock."""
        if self.down:
            raise RedisConnectionError("offline")
        self.ttls.append(time)
        self.values[name] = value
        return True


def test_retry_delays_budget_and_nonretryable_errors() -> None:
    """Full jitter is bounded exponentially, and permanent validation errors are not retried."""
    calls: list[int] = []
    delays: list[float] = []

    def transient() -> str:
        calls.append(1)
        if len(calls) < 3:
            raise OSError("transient")
        return "ok"

    assert (
        RetryPolicy(3, 0.1, 0.15).call(
            transient,
            (OSError,),
            delays.append,
            lambda: 0.5,
        )
        == "ok"
    )
    assert delays == pytest.approx([0.05, 0.075])

    def permanent() -> str:
        raise ValueError("bad input")

    with pytest.raises(ValueError):
        RetryPolicy().call(permanent, (OSError,))
    for policy in [(0, 0, 0), (1, -1, 0), (2, 2, 1)]:
        with pytest.raises(ValueError):
            RetryPolicy(*policy)


def test_breaker_open_half_open_probe_failure_and_recovery() -> None:
    """Advance a fake monotonic clock; verify no dependency call during cooldown."""
    clock = [0.0]
    breaker = CircuitBreaker(2, 10, lambda: clock[0])

    def failure() -> str:
        raise OSError("offline")

    for _ in range(2):
        with pytest.raises(OSError):
            breaker.call(failure, (OSError,))
    assert breaker.state == "open"
    with pytest.raises(CircuitOpen):
        breaker.call(lambda: "not admitted", (OSError,))
    clock[0] = 10

    def probe() -> str:
        assert breaker.state == "half_open"
        with pytest.raises(CircuitOpen):
            breaker.call(lambda: "second probe", (OSError,))
        return "recovered"

    assert breaker.call(probe, (OSError,)) == "recovered"
    assert str(breaker.state) == "closed"
    for _ in range(2):
        with pytest.raises(OSError):
            breaker.call(failure, (OSError,))
    clock[0] += 10
    with pytest.raises(OSError):
        breaker.call(failure, (OSError,))
    assert breaker.state == "open"


def test_cache_identity_corruption_fail_open_and_embedder_wiring() -> None:
    """Compatible repeated inputs skip inference; model/input changes do not reuse vectors."""
    client = MemoryRedis()
    cache = EmbeddingCache(client, 60)
    backend = CacheBackend()
    embedder = Embedder(backend, "fixture@1", dimensions=2, cache=cache)
    assert embedder.embed_query("question", "fixed") == (1.0, 0.0)
    assert embedder.embed_query("question", "fixed") == (1.0, 0.0)
    assert cache.hits == 1 and cache.misses == 1 and client.ttls == [60]
    assert backend.calls == 1
    assert embedder.embed_query("question", "late") == (0.0, 1.0)
    assert backend.calls == 2
    chunks = [Chunk("cached document", "doc", 0, 0, 15, 2, "fixed")]
    assert embedder.embed_chunks(chunks)[0].vector == (1.0, 0.0)
    assert embedder.embed_chunks(chunks)[0].vector == (1.0, 0.0)
    assert backend.calls == 3
    assert cache.get("fixture@2", "question", 2) is None
    key = cache.key("broken", "q", 2)
    for payload in ['{"wrong":1}', "[NaN,0]", "[0,0]", "[true,0]", "not-json"]:
        client.values[key] = payload
        assert cache.get("broken", "q", 2) is None
    client.down = True
    assert cache.get_or_compute("new", "q", 2, lambda: (1.0, 0.0)) == (1.0, 0.0)
    assert cache.errors >= 7


def test_stale_inflight_failure_cannot_reopen_recovered_generation() -> None:
    """A delayed old request must not corrupt the state after a newer recovery probe."""
    clock = [0.0]
    breaker = CircuitBreaker(1, 1, lambda: clock[0])
    admitted, release = Event(), Event()

    def old_call() -> str:
        admitted.set()
        if not release.wait(timeout=5):
            raise AssertionError("Test failed to release old call")
        raise OSError("old failure")

    def fail() -> str:
        raise OSError("current failure")

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(breaker.call, old_call, (OSError,))
        try:
            assert admitted.wait(timeout=5)
            with pytest.raises(OSError):
                breaker.call(fail, (OSError,))
            clock[0] = 1
            assert breaker.call(lambda: "recovered", (OSError,)) == "recovered"
        finally:
            release.set()
        with pytest.raises(OSError):
            future.result(timeout=5)
    assert breaker.state == "closed"


def test_half_open_validation_failure_does_not_strand_probe() -> None:
    """Nonretryable probe errors propagate but leave an admissible future recovery path."""
    clock = [0.0]
    breaker = CircuitBreaker(1, 1, lambda: clock[0])

    def fail() -> str:
        raise OSError("offline")

    with pytest.raises(OSError):
        breaker.call(fail, (OSError,))
    clock[0] = 1

    def invalid() -> str:
        raise ValueError("bad provider contract")

    with pytest.raises(ValueError):
        breaker.call(invalid, (OSError,))
    assert breaker.state == "open"
    clock[0] = 2
    assert breaker.call(lambda: "ok", (OSError,)) == "ok"
    assert str(breaker.state) == "closed"


@pytest.mark.integration
def test_real_redis_hit_latency_and_expiration() -> None:
    """Use a real Redis cache with a deliberately delayed fake provider, not model timing claims."""
    if os.environ.get("CHUNKFORGE_RUN_REDIS_TESTS") != "1":
        pytest.skip("Set CHUNKFORGE_RUN_REDIS_TESTS=1 for the real Redis check")
    client = Redis.from_url(
        os.environ.get("CHUNKFORGE_TEST_REDIS_URL", "redis://localhost:6379/15"),
        socket_timeout=1,
        socket_connect_timeout=1,
    )
    identity = "integration@" + uuid4().hex
    cache = EmbeddingCache(cast(CacheClient, client), 1)
    calls: list[int] = []

    def delayed_provider() -> tuple[float, ...]:
        calls.append(1)
        sleep(0.08)
        return (1.0, 0.0)

    key = cache.key(identity, "q", 2)
    try:
        client.ping()
        start = perf_counter()
        cache.get_or_compute(identity, "q", 2, delayed_provider)
        miss_ms = (perf_counter() - start) * 1000
        start = perf_counter()
        cache.get_or_compute(identity, "q", 2, delayed_provider)
        hit_ms = (perf_counter() - start) * 1000
        assert len(calls) == 1 and cache.hits == 1
        assert hit_ms < miss_ms
        assert client.ttl(key) in (0, 1)
        sleep(1.1)
        assert cache.get(identity, "q", 2) is None
        print(f"real Redis / delayed provider: miss={miss_ms:.3f}ms hit={hit_ms:.3f}ms")
    finally:
        client.delete(key)
        client.close()
