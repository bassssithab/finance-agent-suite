"""Prototype tenant-scoped usage tracking and aggregation.

Learning/prototype component, same status as platform/tenancy: NOT wired
into app.py, any agent, or SessionService. Built and tested on a simulated
week of usage — no real usage data exists yet.

    record_event(scope, username=…, event_type="agent_run", detail="reconciliation-agent")
    count_by_type(scope, start=…, end=…)          -> {event_type: count}
    activity_by_user(scope, event_type="agent_run") -> {username: count}
    rate_limit_check(scope, username=…, event_type="agent_run", limit=20, window_seconds=3600)
        -> RateLimitStatus(count=…, exceeded=…, remaining=…)   # answers, never enforces

This is NOT the audit log. See the README: audit-log is the permanent,
tamper-evident record of what happened; analytics is a mutable, queryable
usage layer for counting and trends. The two are recorded independently.

`../tenancy` is put on sys.path here (for require_scope / TenantScope).
"""

import sys
from pathlib import Path

_platform = Path(__file__).resolve().parent.parent.parent
for _dep in ("tenancy", "auth", "audit-log"):
    _p = str(_platform / _dep)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tenancy import MissingTenantScope  # noqa: E402  (re-exported: one "no scope" type)

from .models import RateLimitStatus, UsageEvent  # noqa: E402
from .store import AnalyticsStore  # noqa: E402

__all__ = [
    "AnalyticsStore",
    "UsageEvent",
    "RateLimitStatus",
    "MissingTenantScope",
]
