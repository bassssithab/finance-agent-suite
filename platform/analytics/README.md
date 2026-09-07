# platform/analytics

> **Prototype / learning module.** Not wired into `app.py`, any agent, or
> `SessionService`. Built and tested on a simulated week of usage — no real
> usage data exists yet.

A tenant-scoped **usage layer**: record events, then count and aggregate
them.

## analytics is not the audit log

These two are easy to conflate and must never be. They record independently
— one real-world action (a login, an agent run) causes the caller to write
*one audit event and one analytics event*, for different consumers.

| | `platform/audit-log` | `platform/analytics` |
|---|---|---|
| **Question it answers** | "what exactly happened, and can we prove it wasn't altered?" | "how much is happening, and is user Y over a cap?" |
| **Integrity** | hash-chained; SQLite triggers reject `UPDATE`/`DELETE`; `verify_chain()` | plain mutable table — rows can be pruned or re-derived at will |
| **Read pattern** | replay the stream in order; export as an evidence pack | `GROUP BY`, counts, per-user rollups, time windows |
| **Scope** | global — one chain across the whole system | tenant-scoped — every call needs a `TenantScope` |
| **Never used as** | a metrics source (wrong shape; querying it competes with its integrity job) | evidence (mutable, not tamper-evident) |

`analytics` does not import `audit-log` and writes nothing to it.

## Usage

```python
from tenancy import TenancyStore
from analytics import AnalyticsStore

scope = TenancyStore("tenancy.db").scope_for("acme-books")
store = AnalyticsStore("analytics.db")

store.record_event(scope, username="dana.acme", event_type="agent_run",
                   detail="reconciliation-agent")

store.count_by_type(scope, start=week_start, end=week_end)
# -> {"login": 15, "agent_run": 23, "file_uploaded": 2}

store.activity_by_user(scope, event_type="agent_run")
# -> {"dana.acme": 18, "evan.acme": 5}

status = store.rate_limit_check(
    scope, username="dana.acme", event_type="agent_run",
    limit=20, window_seconds=3600,
)
status.count      # events of this type by this user in the last hour
status.exceeded   # count >= limit  — the allowance is used up
status.remaining  # max(0, limit - count)
```

## Guarantees

- **Tenant isolation** — every method calls `tenancy.require_scope(scope)`
  first and composes `WHERE tenant_id = ?` from the scope; `record_event`
  stamps `tenant_id` from the scope (a `tenant_id=` kwarg is a `ValueError`).
  No method returns another tenant's rows. Same pattern as
  `tenancy.ScopedTable` and `file_storage.ScopedFileStore`.
- **`rate_limit_check` answers, it does not enforce** — pure query, records
  nothing, changes nothing. A caller decides whether to allow the action.
  The window is `[now - window_seconds, now]`, inclusive at both edges.
- **`count_by_type` window is half-open `[start, end)`.**
- Every timestamp is stored as an ISO-8601 string (display) and a Unix-epoch
  `REAL` (all range/window math, indexed).

## Not in this prototype

- rollups / retention (materialized daily counts, pruning old rows)
- wiring into `SessionService` / the agents so events are recorded
  automatically; a module that *enforces* the rate-limit answer
- richer queries (breakdown by day, by detail, percentiles), a dashboard

## Development

```bash
cd platform/analytics && ../../.venv/bin/pytest -v
```

`conftest.py` puts `analytics/`, `../tenancy`, `../auth`, and `../audit-log`
on `sys.path` — no install step.
