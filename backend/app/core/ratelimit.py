"""In-process sliding-window rate limiter (no Redis, per PROMPT section 0.1)."""

import threading
import time
from collections import deque


class RateLimiter:
    def __init__(self, max_events: int, window_seconds: float) -> None:
        self.max_events = max_events
        self.window = window_seconds
        self._events: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str, now: float | None = None) -> bool:
        """Registers an event; returns False when the key exceeded the limit (event not counted)."""
        t = time.monotonic() if now is None else now
        with self._lock:
            q = self._events.setdefault(key, deque())
            while q and q[0] <= t - self.window:
                q.popleft()
            if len(q) >= self.max_events:
                return False
            q.append(t)
            if len(self._events) > 50_000:  # noqa: PLR2004 - evita crescimento ilimitado
                self._purge(t)
            return True

    def retry_after(self, key: str, now: float | None = None) -> float:
        t = time.monotonic() if now is None else now
        with self._lock:
            q = self._events.get(key)
            if not q or len(q) < self.max_events:
                return 0.0
            return max(0.0, q[0] + self.window - t)

    def _purge(self, t: float) -> None:
        for key in [k for k, q in self._events.items() if not q or q[-1] <= t - self.window]:
            del self._events[key]
