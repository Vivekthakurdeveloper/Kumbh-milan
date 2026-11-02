import logging
from dataclasses import dataclass

from app.config import Settings
from app.observability.metrics import RATE_LIMIT_REJECTIONS, RATE_LIMITER_ERRORS
from app.ratelimit.backends import LimiterBackend, LimiterUnavailable, MemoryBackend, RedisBackend
from app.ratelimit.policies import Policy, build_rules, policies_for

logger = logging.getLogger("kumbh_setu.ratelimit")


@dataclass(frozen=True)
class Verdict:
    status: int  # 200 allowed, 429 limited, 503 limiter unavailable for a fail-closed policy
    policy: str | None = None
    retry_after: int = 0


ALLOWED = Verdict(200)


class RateLimiter:
    def __init__(self, settings: Settings, backend: LimiterBackend) -> None:
        self.enabled = settings.rate_limit_enabled
        self.backend = backend
        self.fallback = MemoryBackend()
        self.rules, self.general = build_rules(settings)

    def check(self, method: str, path: str, *, client_ip: str | None, user_id: str | None) -> Verdict:
        if not self.enabled:
            return ALLOWED
        for policy in policies_for(self.rules, self.general, method, path):
            verdict = self._check_policy(policy, client_ip=client_ip, user_id=user_id)
            if verdict.status != 200:
                return verdict
        return ALLOWED

    def _check_policy(self, policy: Policy, *, client_ip: str | None, user_id: str | None) -> Verdict:
        subject = f"u:{user_id}" if policy.key_by == "user_or_ip" and user_id else f"ip:{client_ip or 'unknown'}"
        key = f"{policy.name}:{subject}"
        try:
            decision = self.backend.hit(key, policy.limit, policy.window_seconds)
        except LimiterUnavailable as exc:
            if policy.fail_closed:
                RATE_LIMITER_ERRORS.labels(policy.name, "rejected").inc()
                logger.error("Shared rate limiter unavailable; failing closed", extra={"policy": policy.name, "error": str(exc)})
                return Verdict(503, policy.name, 5)
            RATE_LIMITER_ERRORS.labels(policy.name, "local_fallback").inc()
            logger.warning("Shared rate limiter unavailable; using local limits", extra={"policy": policy.name})
            decision = self.fallback.hit(key, policy.limit, policy.window_seconds)
        if not decision.allowed:
            RATE_LIMIT_REJECTIONS.labels(policy.name).inc()
            return Verdict(429, policy.name, decision.retry_after)
        return ALLOWED


def build_limiter(settings: Settings) -> RateLimiter:
    if settings.rate_limit_backend == "redis":
        if not settings.redis_url:
            raise RuntimeError("RATE_LIMIT_BACKEND=redis requires REDIS_URL")
        backend: LimiterBackend = RedisBackend(settings.redis_url, settings.redis_timeout_ms)
    else:
        backend = MemoryBackend()
    return RateLimiter(settings, backend)
