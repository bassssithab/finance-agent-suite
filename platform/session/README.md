# platform/session

> **Prototype / learning module.** Not wired into `app.py` or any agent.
> Built and tested entirely on fictional users/organizations. Same status
> as `platform/auth` and `platform/tenancy`.

Combines authentication and tenancy into one usable flow. It owns no
storage — it orchestrates an existing `auth.AuthStore`,
`tenancy.TenancyStore`, and `audit_log.AuditLogStore`.

## The one-call flow

```python
from audit_log import AuditLogStore
from auth import AuthStore
from tenancy import TenancyStore
from session import SessionService, AuthenticatedSession, AuthFailure

svc = SessionService(AuthStore("auth.db"), TenancyStore("tenancy.db"), AuditLogStore("audit.db"))

result = svc.authenticate("dana.acme", password)          # + totp_code=… if MFA is on
match result:
    case AuthenticatedSession() as s:
        s.token       # hold this for later requests
        s.user        # auth.User
        s.scope       # tenancy.TenantScope — ready to hand to a ScopedTable
        s.tenant_id   # == s.scope.tenant_id
    case AuthFailure.BAD_CREDENTIALS:
        ...  # wrong username or password (not told which)
    case AuthFailure.MFA_REQUIRED:
        ...  # password ok, account has TOTP MFA — re-call with totp_code=…
    case AuthFailure.MFA_INVALID:
        ...  # code was wrong, expired, or a replay of one already used
    case AuthFailure.NO_TENANT_ASSIGNED:
        ...  # all factors ok, but user isn't in an org yet
```

On a later request, re-derive the same bundle from the token alone:

```python
result = svc.validate(token)   # AuthenticatedSession | AuthFailure.INVALID_TOKEN
                               #                       | AuthFailure.NO_TENANT_ASSIGNED
```

## Outcomes

| Outcome | `authenticate` | `validate` |
|---|---|---|
| success | `AuthenticatedSession` | `AuthenticatedSession` |
| wrong username / password | `AuthFailure.BAD_CREDENTIALS` | — |
| password ok, MFA on, no code | `AuthFailure.MFA_REQUIRED` | — |
| password ok, code wrong / expired / replayed | `AuthFailure.MFA_INVALID` | — |
| unknown / malformed / expired / logged-out token | — | `AuthFailure.INVALID_TOKEN` |
| valid identity, no tenant membership | `AuthFailure.NO_TENANT_ASSIGNED` | `AuthFailure.NO_TENANT_ASSIGNED` |

Every non-success is a distinct, self-describing `AuthFailure` value —
never a bare `None`, never a session with a missing scope.

`authenticate` checks each factor itself — password, then TOTP MFA if the
account has it, then tenant membership — and issues the session token
**last**. So a `NO_TENANT_ASSIGNED` (or any earlier failure) leaves no token
behind; there is nothing to roll back. `validate` re-derives the bundle
from a live token and returns `NO_TENANT_ASSIGNED` only if the user lost
their tenant membership after the token was issued.

Every `authenticate` / `validate` / `logout` writes one event to the
injected `AuditLogStore`; the password and any TOTP code are never logged.

## Not in this prototype

- token refresh, "switch tenant", multi-tenant users, revocation lists,
  FastAPI middleware that turns a request header into an
  `AuthenticatedSession`

## Development

```bash
# from repo root, one-time setup
python3 -m venv .venv
.venv/bin/pip install pytest

# run tests
cd platform/session && ../../.venv/bin/pytest -v
```

No install step is needed. `conftest.py` puts `session/`, `../auth`,
`../tenancy`, and `../audit-log` on `sys.path` for the test run.
