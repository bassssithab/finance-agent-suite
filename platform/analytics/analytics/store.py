"""SQLite-backed usage store for the prototype analytics layer.

Same construct/close shape as the other platform stores. Tenant-scoped
exactly like tenancy.ScopedTable / file_storage.ScopedFileStore: every
method calls tenancy.require_scope(scope) first and composes
`WHERE tenant_id = ?` from the scope itself — there is no method that
returns another tenant's rows, and record_event stamps tenant_id from the
scope (a `tenant_id=` kwarg is a loud error).

Deliberately NOT like audit-log: the usage_events table is plain and
mutable — no hash chain, no append-only triggers. Analytics rows exist to
be counted, grouped, and (eventually) pruned. Tamper-evidence is the audit
log's job, on a separate stream. See the README.

Every timestamp is stored twice: `occurred_at` (ISO-8601 UTC string, for
display) and `occurred_at_epoch` (Unix seconds, REAL) — all range and
window arithmetic uses the epoch column, which is unambiguous and indexed.
"""

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union

_tenancy_dir = Path(__file__).resolve().parent.parent.parent / "tenancy"
if str(_tenancy_dir) not in sys.path:
    sys.path.insert(0, str(_tenancy_dir))

from tenancy import require_scope  # noqa: E402

from .models import RateLimitStatus, UsageEvent

_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    username TEXT NOT NULL,
    event_type TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    occurred_at TEXT NOT NULL,
    occurred_at_epoch REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_usage_tenant_time
    ON usage_events (tenant_id, occurred_at_epoch);
CREATE INDEX IF NOT EXISTS ix_usage_tenant_user_type_time
    ON usage_events (tenant_id, username, event_type, occurred_at_epoch);
"""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AnalyticsStore:
    def __init__(self, db_path: Union[str, Path]):
        self.db_path = str(db_path)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # -- record ---------------------------------------------------------

    def record_event(
        self,
        scope,
        *,
        username: str,
        event_type: str,
        detail: str = "",
        now: Optional[datetime] = None,
        **extra,
    ) -> int:
        """Record one usage event for the scope's tenant. Returns the new id.

        tenant_id is taken from the scope; any extra kwarg (e.g. tenant_id=)
        is a loud ValueError.
        """
        require_scope(scope)
        if extra:
            raise ValueError(
                f"unexpected fields {sorted(extra)}; tenant_id comes from the scope"
            )
        stamp = now or _utcnow()
        cursor = self._conn.execute(
            "INSERT INTO usage_events "
            "(tenant_id, username, event_type, detail, occurred_at, occurred_at_epoch) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (scope.tenant_id, username, event_type, detail,
             stamp.isoformat(), stamp.timestamp()),
        )
        self._conn.commit()
        return cursor.lastrowid

    # -- aggregation ---------------------------------------------------

    def count_by_type(self, scope, *, start: datetime, end: datetime) -> dict:
        """Count of events by event_type in the half-open window [start, end)
        for the scope's tenant."""
        require_scope(scope)
        rows = self._conn.execute(
            "SELECT event_type, COUNT(*) FROM usage_events "
            "WHERE tenant_id = ? AND occurred_at_epoch >= ? AND occurred_at_epoch < ? "
            "GROUP BY event_type",
            (scope.tenant_id, start.timestamp(), end.timestamp()),
        ).fetchall()
        return {event_type: count for event_type, count in rows}

    def activity_by_user(
        self,
        scope,
        *,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        event_type: Optional[str] = None,
    ) -> dict:
        """{username: event count} for the scope's tenant, optionally within
        [start, end) and/or restricted to one event_type."""
        require_scope(scope)
        clauses = ["tenant_id = ?"]
        params = [scope.tenant_id]
        if start is not None:
            clauses.append("occurred_at_epoch >= ?")
            params.append(start.timestamp())
        if end is not None:
            clauses.append("occurred_at_epoch < ?")
            params.append(end.timestamp())
        if event_type is not None:
            clauses.append("event_type = ?")
            params.append(event_type)
        rows = self._conn.execute(
            f"SELECT username, COUNT(*) FROM usage_events "
            f"WHERE {' AND '.join(clauses)} GROUP BY username",
            params,
        ).fetchall()
        return {username: count for username, count in rows}

    def rate_limit_check(
        self,
        scope,
        *,
        username: str,
        event_type: str,
        limit: int,
        window_seconds: int,
        now: Optional[datetime] = None,
    ) -> RateLimitStatus:
        """Count this user's `event_type` events in the last `window_seconds`
        (for the scope's tenant) and report whether that is at or over
        `limit`. Pure query — records nothing, enforces nothing.

        The window is [now - window_seconds, now], inclusive at both edges.
        """
        require_scope(scope)
        now_epoch = (now or _utcnow()).timestamp()
        (count,) = self._conn.execute(
            "SELECT COUNT(*) FROM usage_events "
            "WHERE tenant_id = ? AND username = ? AND event_type = ? "
            "AND occurred_at_epoch >= ? AND occurred_at_epoch <= ?",
            (scope.tenant_id, username, event_type,
             now_epoch - window_seconds, now_epoch),
        ).fetchone()
        return RateLimitStatus(
            username=username,
            event_type=event_type,
            window_seconds=window_seconds,
            limit=limit,
            count=count,
            exceeded=count >= limit,
            remaining=max(0, limit - count),
        )

    # -- raw listing (for tests / a future UI) -----------------------

    def all_events(self, scope) -> list:
        """Every event for the scope's tenant, oldest first."""
        require_scope(scope)
        rows = self._conn.execute(
            "SELECT id, tenant_id, username, event_type, detail, occurred_at "
            "FROM usage_events WHERE tenant_id = ? "
            "ORDER BY occurred_at_epoch, id",
            (scope.tenant_id,),
        ).fetchall()
        return [
            UsageEvent(id=r[0], tenant_id=r[1], username=r[2], event_type=r[3],
                       detail=r[4], occurred_at=r[5])
            for r in rows
        ]
