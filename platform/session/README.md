# platform/session

> **Prototype / learning module.** Not wired into `app.py` or any agent.
> Built and tested entirely on fictional users/organizations. Same status
> as `platform/auth` and `platform/tenancy`.

Combines authentication and tenancy into one usable flow. It owns no
storage — it orchestrates an existing `auth.AuthStore` and
`tenancy.TenancyStore`.

## The one-call flow

```python
from auth import AuthStore
from tenancy import TenancyStore
from session import SessionService, AuthenticatedSession, AuthFailure

svc = SessionService(AuthStore("auth.db"), TenancyStore("tenancy.db"))

result = svc.authenticate("dana.acme", password)
match result:
    case AuthenticatedSession() as s:
        s.token       # hold this for later requests
        s.user        # auth.User
        s.scope       # tenancy.TenantScope — ready to hand to a ScopedTable
        s.tenant_id   # == s.scope.tenant_id
    case AuthFailure.BAD_CREDENTIALS:
        ...  # wrong username or password (not told which)
    case AuthFailure.NO_TENANT_ASSIGNED:
        ...  # login ok, but user isn't in an org yet — send them to org setup
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
| unknown / malformed / expired / logged-out token | — | `AuthFailure.INVALID_TOKEN` |
| valid identity, no tenant membership | `AuthFailure.NO_TENANT_ASSIGNED` (token rolled back) | `AuthFailure.NO_TENANT_ASSIGNED` (token left intact) |

Every non-success is a distinct, self-describing `AuthFailure` value —
never a bare `None`, never a session with a missing scope.

**The no-tenant case is first-class.** A freshly-created user who hasn't
been assigned to an org is a real, expected state. `authenticate` returns
`NO_TENANT_ASSIGNED` *and* rolls back the login token it just issued, so an
unassigned user is never left holding a usable session. `validate` returns
the same value but leaves the token intact — the token is genuinely valid,
it's the tenancy that's missing (e.g. the user was assigned to an org only
after the token was issued).

## Not in this prototype

- audit-log wiring — the deliberate next step if this graduates
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

No install step is needed. `conftest.py` puts `session/`, `../auth`, and
`../tenancy` on `sys.path` for the test run.
