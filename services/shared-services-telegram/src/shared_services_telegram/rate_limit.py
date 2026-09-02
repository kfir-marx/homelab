from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class TokenBucket:
    capacity: float
    refill_per_second: float
    tokens: float = field(init=False)
    updated_at: float = field(default_factory=time.monotonic)

    def __post_init__(self) -> None:
        self.tokens = self.capacity

    def take(self) -> bool:
        now = time.monotonic()
        self.tokens = min(
            self.capacity, self.tokens + (now - self.updated_at) * self.refill_per_second
        )
        self.updated_at = now
        if self.tokens < 1:
            return False
        self.tokens -= 1
        return True


class RateLimits:
    def __init__(self, per_user_per_minute: int, global_per_minute: int) -> None:
        self._per_user_rate = per_user_per_minute
        self._users: dict[tuple[int, str], TokenBucket] = {}
        self._global = TokenBucket(global_per_minute, global_per_minute / 60)
        self._category_limits = {
            "callback": (20, 20 / 60),
            "url": (6, 6 / 60),
            "generation": (3, 3 / 300),
            "file": (3, 3 / 300),
        }

    def allow(self, user_id: int, category: str = "update") -> bool:
        if not self._global.take():
            return False
        capacity, refill = self._category_limits.get(
            category, (self._per_user_rate, self._per_user_rate / 60)
        )
        bucket = self._users.setdefault((user_id, category), TokenBucket(capacity, refill))
        return bucket.take()
