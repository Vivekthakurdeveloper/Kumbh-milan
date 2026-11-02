"""
Sliding-window-counter limiter (two fixed windows, the previous one weighted by overlap).
Redis executes the whole check-and-increment in one Lua script, so every replica shares one atomic count.
"""

import math
import threading
import time
from dataclasses import dataclass
from typing import Protocol

import redis
from redis.exceptions import RedisError


@dataclass(frozen=True)
class Decision:
    allowed: bool
    remaining: int
    retry_after: int


class LimiterUnavailable(Exception):
    pass


class LimiterBackend(Protocol):
    def hit(self, key: str, limit: int, window_seconds: int) -> Decision: ...

    def ping(self) -> None: ...


def _decide(previous: int, current: int, limit: int, window_ms: int, elapsed_ms: int) -> tuple[bool, float]:
    estimate = previous * (window_ms - elapsed_ms) / window_ms + current
    return estimate + 1 <= limit, estimate


class MemoryBackend:
    """Per-process. Used locally, in tests, and as the degraded fallback for fail-open policies."""

    def __init__(self) -> None:
        self._counts: dict[tuple[str, int], int] = {}
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int, window_seconds: int) -> Decision:
        window_ms = window_seconds * 1000
        now_ms = int(time.time() * 1000)
        window_id, elapsed = divmod(now_ms, window_ms)
        with self._lock:
            if len(self._counts) > 50_000:
                self._counts = {k: v for k, v in self._counts.items() if k[1] >= window_id - 1}
            previous = self._counts.get((key, window_id - 1), 0)
            current = self._counts.get((key, window_id), 0)
            allowed, estimate = _decide(previous, current, limit, window_ms, elapsed)
            if allowed:
                self._counts[(key, window_id)] = current + 1
        return Decision(allowed, max(0, int(limit - estimate - 1)), max(1, math.ceil((window_ms - elapsed) / 1000)))

    def ping(self) -> None:
        return None


_LUA = """
local t = redis.call('TIME')
local now_ms = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local window_ms = tonumber(ARGV[2])
local limit = tonumber(ARGV[1])
local window_id = math.floor(now_ms / window_ms)
local elapsed = now_ms - window_id * window_ms
local cur_key = KEYS[1] .. ':' .. window_id
local prev_key = KEYS[1] .. ':' .. (window_id - 1)
local current = tonumber(redis.call('GET', cur_key) or '0')
local previous = tonumber(redis.call('GET', prev_key) or '0')
local estimate = previous * (window_ms - elapsed) / window_ms + current
local retry = math.ceil((window_ms - elapsed) / 1000)
if estimate + 1 > limit then
  return {0, 0, retry}
end
redis.call('INCR', cur_key)
redis.call('PEXPIRE', cur_key, window_ms * 2)
return {1, math.floor(limit - estimate - 1), retry}
"""


class RedisBackend:
    def __init__(self, url: str, timeout_ms: int = 250, prefix: str = "kumbh:rl") -> None:
        timeout = timeout_ms / 1000
        self._client = redis.Redis.from_url(
            url, socket_timeout=timeout, socket_connect_timeout=timeout, health_check_interval=30
        )
        self._script = self._client.register_script(_LUA)
        self._prefix = prefix

    def hit(self, key: str, limit: int, window_seconds: int) -> Decision:
        try:
            allowed, remaining, retry = self._script(keys=[f"{self._prefix}:{{{key}}}"], args=[limit, window_seconds * 1000])
        except RedisError as exc:
            raise LimiterUnavailable(type(exc).__name__) from exc
        return Decision(bool(allowed), int(remaining), max(1, int(retry)))

    def ping(self) -> None:
        try:
            self._client.ping()
        except RedisError as exc:
            raise LimiterUnavailable(type(exc).__name__) from exc
