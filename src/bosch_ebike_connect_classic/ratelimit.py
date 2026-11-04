from __future__ import annotations

import time
from collections import defaultdict, deque


class RateLimiter:
    """In-memory sliding window. Single-process only (fine for one container)."""

    def __init__(self, limit: int, window_s: float) -> None:
        self.limit, self.window = limit, window_s
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def hit(self, key: str) -> bool:
        now = time.monotonic()
        q = self._hits[key]
        while q and q[0] <= now - self.window:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(now)
        if len(self._hits) > 10_000:  # bound memory
            for k in [k for k, v in self._hits.items() if not v or v[-1] <= now - self.window]:
                self._hits.pop(k, None)
        return True
