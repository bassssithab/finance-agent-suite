# platform/tenancy

> **Prototype / learning module.** Not wired into `app.py` or any agent.
> Built and tested entirely on fictional organizations. Same status as
> `platform/auth`.

A basic multi-tenancy layer for the platform chassis:

- **Tenant model** — a unique `tenant_id` slug, a `display_name`,
  `created_at`.
- **One tenant per user** — `assign_user()` associates an `auth.User` (or a
  bare username) with exactly one tenant. `memberships.username` is the
  table's primary key, so a second assignment is structurally impossible;
  it raises `AlreadyAssigned` rather than silently moving the user.
- **`TenantScope` + `ScopedTable`** — the tenant filter made structurally
  unforgettable (see below).

## The scoped data-access pattern

`TenantScope` is a frozen capability object. The only way to get one is
`TenancyStore.scope_for(tenant_id)` or `scope_for_user(user)`, both of which
verify the tenant exists first.

`ScopedTable` wraps one SQLite table whose rows carry a `tenant_id` column:

```python
from tenancy import TenancyStore, ScopedTable

store = TenancyStore("tenancy.db")
store.create_tenant("acme-books", "Acme Bookkeeping LLC")
store.assign_user(some_auth_user, "acme-books")

scope = store.scope_for_user(some_auth_user)      # -> TenantScope("acme-books")

notes = ScopedTable(conn, "ledger_notes")
notes.insert(scope, author="dana.acme", text="...")   # tenant_id stamped from scope
notes.all(scope)                                        # only this tenant's rows
notes.get(scope, row_id)                                # None if row belongs to another tenant
```

What makes cross-tenant access hard to do by accident:

1. Every read/write **requires** a `TenantScope` as its first argument.
2. The method composes `WHERE tenant_id = ?` itself — the caller never
   writes the filter and cannot omit it.
3. `insert` takes `tenant_id` **only** from the scope; passing a
   `tenant_id` in the row fields is a loud `ValueError`, not a silent
   override.
4. No public method returns rows without a scope; a missing or wrong-typed
   scope raises `MissingTenantScope`.

This is the hand-rolled stand-in for what `docs/ARCHITECTURE.md` calls
Postgres "row-level security" in a real deployment.

`require_scope(scope)` is the shared, exported definition of "a valid tenant
scope" — `ScopedTable` uses it, and so does `platform/file-storage`'s
`ScopedFileStore`, so there is one rule and one exception type
(`MissingTenantScope`) for "no scope" across the platform.

## Not in this prototype

- audit-log wiring — the deliberate next step if this graduates
- tenant-scoped `AuthStore`, tenant admins, cross-tenant superusers,
  tenant deletion, per-tenant DB isolation, FastAPI middleware that derives
  the scope from a session token

## Development

```bash
# from repo root, one-time setup
python3 -m venv .venv
.venv/bin/pip install pytest

# run tests
cd platform/tenancy && ../../.venv/bin/pytest -v
```

No install step is needed. `conftest.py` puts `tenancy/` on `sys.path`, and
`tenancy/__init__.py` adds `../auth` (which itself adds `../approvals` and
`../audit-log`) so `from auth import ...` resolves without a separate
install.
