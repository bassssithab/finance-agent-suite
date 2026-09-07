"""Data model for the prototype analytics layer."""

from dataclasses import dataclass

__all__ = ["UsageEvent", "RateLimitStatus"]


@dataclass(frozen=True)
class UsageEvent:
    """One recorded usage event. Mutable at the store level (analytics rows
    can be pruned or re-derived) — this is not an audit record."""

    id: int
    tenant_id: str
    username: str
    event_type: str
    detail: str
    occurred_at: str  # ISO-8601 UTC


@dataclass(frozen=True)
class RateLimitStatus:
    """The answer to 'has this user done too much of X lately?' — a pure
    query result. It enforces nothing; a caller decides what to do with it.
    """

    username: str
    event_type: str
    window_seconds: int
    limit: int
    count: int          # matching events currently inside the window
    exceeded: bool       # count >= limit — the allowance is used up
    remaining: int       # max(0, limit - count)
