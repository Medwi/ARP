"""
Pluggable rate-limit storage — in-memory (single worker) or Redis (multi-worker).
"""

from __future__ import annotations

import time
import threading
import uuid
from abc import ABC, abstractmethod
from collections import defaultdict, deque
from typing import Optional


class RateLimitStore(ABC):
    @abstractmethod
    def check_and_record(self, key: str, limit: int, window: int) -> tuple[bool, int]:
        """Return (is_limited, retry_after_seconds)."""


class MemoryRateLimitStore(RateLimitStore):
    """Sliding window per key — correct for uvicorn --workers 1 only."""

    def __init__(self) -> None:
        self._windows: defaultdict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def check_and_record(self, key: str, limit: int, window: int) -> tuple[bool, int]:
        now = time.time()
        cutoff = now - window

        with self._lock:
            dq = self._windows[key]
            while dq and dq[0] < cutoff:
                dq.popleft()

            if len(dq) >= limit:
                retry_after = int(dq[0] + window - now) + 1
                return True, retry_after

            dq.append(now)
            return False, 0


class RedisRateLimitStore(RateLimitStore):
    """
    Shared sliding window via Redis sorted sets.
    Safe across multiple uvicorn workers or backend replicas.
    """

    def __init__(self, url: str) -> None:
        import redis

        self._redis = redis.from_url(url, decode_responses=True)
        self._prefix = "arp:rl:"

    def check_and_record(self, key: str, limit: int, window: int) -> tuple[bool, int]:
        now = time.time()
        redis_key = f"{self._prefix}{key}"
        member = f"{now}:{uuid.uuid4().hex[:8]}"

        pipe = self._redis.pipeline()
        pipe.zremrangebyscore(redis_key, 0, now - window)
        pipe.zadd(redis_key, {member: now})
        pipe.zcard(redis_key)
        pipe.expire(redis_key, window + 5)
        _, _, count, _ = pipe.execute()

        if count > limit:
            oldest = self._redis.zrange(redis_key, 0, 0, withscores=True)
            if oldest:
                retry_after = int(oldest[0][1] + window - now) + 1
            else:
                retry_after = window
            self._redis.zrem(redis_key, member)
            return True, max(retry_after, 1)

        return False, 0


_store: Optional[RateLimitStore] = None


def get_rate_limit_store() -> tuple[RateLimitStore, str]:
    """Return (store, backend_label) — memory or redis."""
    global _store
    from backend.config import get_rate_limit_redis_url, rate_limit_backend

    backend = rate_limit_backend()
    if _store is not None:
        return _store, backend

    if backend == "redis":
        url = get_rate_limit_redis_url()
        if not url:
            print("[RATE LIMIT] RATE_LIMIT_BACKEND=redis but REDIS_URL unset — using memory")
            _store = MemoryRateLimitStore()
            return _store, "memory (redis misconfigured)"

        try:
            store = RedisRateLimitStore(url)
            store._redis.ping()
            _store = store
            return _store, "redis"
        except Exception as exc:
            print(f"[RATE LIMIT] Redis unavailable ({exc}) — falling back to memory")
            _store = MemoryRateLimitStore()
            return _store, "memory (redis fallback)"

    _store = MemoryRateLimitStore()
    return _store, "memory"
