from __future__ import annotations

import time
from collections import deque


class RateLimiter:
    """In-memory sliding window. Single-process only (fine for one container).

    `hit()` records an event and says whether it is within the limit; `allowed()` only asks.
    Memory is bounded: expired keys are swept regularly and the table has a hard cap, beyond
    which unknown keys are refused (fail closed) instead of growing without limit.
    A shared backend (e.g. Redis) would replace this class; the callers only use these methods.
    """

    def __init__(self, limit: int, window_s: float, max_keys: int = 50_000) -> None:
        self.limit, self.window, self.max_keys = limit, window_s, max_keys
        self._hits: dict[str, deque[float]] = {}
        self._next_sweep = time.monotonic() + window_s

    def _sweep(self, now: float) -> None:
        cutoff = now - self.window
        for k in [k for k, v in self._hits.items() if not v or v[-1] <= cutoff]:
            del self._hits[k]
        self._next_sweep = now + min(self.window, 60.0)

    def _recent(self, key: str, now: float) -> deque[float] | None:
        q = self._hits.get(key)
        if q is not None:
            while q and q[0] <= now - self.window:
                q.popleft()
        return q

    def allowed(self, key: str) -> bool:
        q = self._recent(key, time.monotonic())
        return q is None or len(q) < self.limit

    def hit(self, key: str) -> bool:
        now = time.monotonic()
        if now >= self._next_sweep:
            self._sweep(now)
        q = self._recent(key, now)
        if q is None:
            if len(self._hits) >= self.max_keys:
                self._sweep(now)
                if len(self._hits) >= self.max_keys:
                    return False
            q = self._hits[key] = deque()
        if len(q) >= self.limit:
            return False
        q.append(now)
        return True
