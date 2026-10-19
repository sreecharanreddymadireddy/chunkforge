"""Bounded exponential backoff with full jitter for idempotent dependency calls."""

import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class RetryPolicy:
    """Count total attempts, cap delay, and inject clocks for fast deterministic tests."""

    attempts: int = 3
    base_delay: float = 0.1
    max_delay: float = 1.0

    def __post_init__(self) -> None:
        """Reject invalid retry budgets instead of silently looping forever."""
        if self.attempts < 1 or self.base_delay < 0 or self.max_delay < self.base_delay:
            raise ValueError("Invalid retry policy")

    def call(
        self,
        operation: Callable[[], T],
        retry_on: tuple[type[Exception], ...],
        sleep: Callable[[float], None] = time.sleep,
        random_fraction: Callable[[], float] = random.random,
    ) -> T:
        """Retry only explicitly classified failures; never sleep after the last attempt."""
        for attempt in range(self.attempts):
            try:
                return operation()
            except retry_on:
                if attempt + 1 == self.attempts:
                    raise
                cap = min(self.max_delay, self.base_delay * 2 ** min(attempt, 30))
                sleep(cap * random_fraction())
        raise AssertionError("Positive attempts guarantee a return or exception")
