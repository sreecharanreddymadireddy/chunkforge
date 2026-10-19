"""Thread-safe circuit breaker with a single half-open recovery probe."""

import time
from collections.abc import Callable
from threading import Lock
from typing import Literal, TypeVar

T = TypeVar("T")
State = Literal["closed", "open", "half_open"]


class CircuitOpen(RuntimeError):
    """A dependency is cooling down or already has a recovery probe in progress."""


class CircuitBreaker:
    """Count exhausted logical calls, not each retry; use monotonic time for cooldown."""

    def __init__(
        self,
        threshold: int = 3,
        recovery_seconds: float = 30,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if threshold < 1 or recovery_seconds <= 0:
            raise ValueError("Require a positive failure threshold and recovery interval")
        self.threshold = threshold
        self.recovery_seconds = recovery_seconds
        self.clock = clock
        self._state: State = "closed"
        self._failures = 0
        self._opened_at = 0.0
        self._generation = 0
        self._lock = Lock()

    @property
    def state(self) -> State:
        """Expose observed state; transition to half-open occurs only when admitting a probe."""
        with self._lock:
            return self._state

    def call(
        self,
        operation: Callable[[], T],
        failure_types: tuple[type[Exception], ...],
    ) -> T:
        """Run outside the lock; ignore stale completions from an older closed generation."""
        with self._lock:
            if self._state == "open":
                if self.clock() - self._opened_at < self.recovery_seconds:
                    raise CircuitOpen("Dependency cooldown")
                self._state = "half_open"
                probe = True
            elif self._state == "half_open":
                raise CircuitOpen("Recovery probe already in progress")
            else:
                probe = False
            generation = self._generation
        try:
            result = operation()
        except BaseException as exc:
            with self._lock:
                if generation == self._generation:
                    if isinstance(exc, failure_types):
                        self._failures += 1
                        if probe or self._failures >= self.threshold:
                            self._state = "open"
                            self._opened_at = self.clock()
                            self._generation += 1
                    elif probe:
                        # A validation/programming failure must not strand a half-open probe.
                        self._state = "open"
                        self._opened_at = self.clock()
                        self._generation += 1
            raise
        with self._lock:
            if generation == self._generation:
                self._state = "closed"
                self._failures = 0
                if probe:
                    self._generation += 1
        return result
