# platform/auth

> **Prototype / learning module.** Not wired into `app.py` or any agent.
> Built and tested entirely on fictional users. Do not use for anything
> real yet.

A basic authentication/login layer for the platform chassis:

- **User model** — username, a securely-hashed password (PBKDF2-HMAC-SHA256
  via `hashlib`, never plaintext), and a `Role` reused from
  `platform/approvals` (`preparer` / `reviewer` / `approver`).
- **SQLite-backed store** (`AuthStore`) — same construct/`close()` pattern
  as `audit_log.AuditLogStore`. `create_user()` registers an account;
  `verify_login()` checks a username + password.
- **Enumeration-safe login** — a wrong password and an unknown username
  return the same `False` and do the same amount of hashing work
  (unknown usernames are verified against a fixed dummy hash), so neither
  the result nor the timing reveals which usernames exist.
- **Sessions** — `login()` issues a random `secrets.token_urlsafe` token on
  success. Only the SHA-256 of the token is persisted, so a dump of the
  `sessions` table cannot be replayed. `validate_token()` returns the owning
  `User` or `None` for an unknown, malformed, or expired token.
  `logout()` invalidates a token.

## Usage

```python
from datetime import datetime, timedelta, timezone
from auth import AuthStore, Role

store = AuthStore("auth.db")
store.create_user("ada.ledger", "correct-horse-battery-staple", Role.APPROVER)

token = store.login("ada.ledger", "correct-horse-battery-staple")
if token is None:
    ...  # bad username or bad password — caller can't tell which

user = store.validate_token(token)     # -> User, or None if invalid/expired
store.logout(token)
```

`now=` is injectable on `create_user`, `login`, and `validate_token` purely
so session expiry is testable without sleeping.

## Not in this prototype

- audit-log wiring (login success/failure events) — the deliberate next
  step if this graduates from prototype
- password strength rules, rate limiting / lockout, token refresh,
  expired-session cleanup, FastAPI routes

## Development

```bash
# from repo root, one-time setup
python3 -m venv .venv
.venv/bin/pip install pytest

# run tests
cd platform/auth && ../../.venv/bin/pytest -v
```

No install step is needed. `conftest.py` puts `auth/` on `sys.path` for the
test run, and `auth/__init__.py` adds `../approvals` (which itself adds
`../audit-log`) so `from approvals import Role` resolves without a separate
install.
