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

## TOTP multi-factor auth

Optional, opt-in per user. `auth.totp` is a hand-rolled RFC 6238
implementation (SHA-1 / 6 digits / 30 s) — no dependency, and its
interoperability is proven against the **RFC 6238 published test vectors**
in `tests/test_totp.py`, so any real authenticator app (Google
Authenticator, Authy, 1Password, …) accepts these secrets.

```python
from auth import AuthStore, MfaService, totp

mfa = MfaService(store, audit_log=audit_log)   # audit_log REQUIRED

enrollment = mfa.enable("ada.ledger")
enrollment.secret            # base32 — for manual entry
enrollment.provisioning_uri  # otpauth://… — render as a QR code

mfa.verify("ada.ledger", "123456")   # -> True / False; MfaNotEnabled if off
mfa.disable("ada.ledger")
```

Once enabled, **password alone no longer logs the user in** —
`store.login(u, p)` returns `None` until called as
`store.login(u, p, totp_code=…)` with a valid code, and
`session.SessionService.authenticate(u, p, totp_code=…)` returns
`AuthFailure.MFA_REQUIRED` / `MFA_INVALID` accordingly.

- **Drift** — the current 30 s step ±1 is accepted (~±30 s of clock skew).
- **Replay protection** — a code, once accepted, burns its time-step
  (`mfa_enrollments.last_used_step`); reusing it (or an older still-in-window
  code) returns `False` / `MFA_INVALID`, audited with reason `reused`. This
  is shared atomically between `login()` and `MfaService.verify()`.
- **Secret at rest** — TOTP verification needs the actual shared secret, so
  unlike passwords and tokens it is stored **plaintext** in
  `mfa_enrollments.secret`. In production: encrypt at rest (KMS / app-level).
  The secret and the `otpauth://` URI are returned once from `enable()` and
  **never written to the audit log**.
- **Audited** — `MfaService` writes `auth.mfa.enabled` / `.disabled` /
  `.verify_succeeded` / `.verify_failed` (with `reason`) to the injected
  `AuditLogStore`; `SessionService` audits the login-time MFA outcome.
  `AuthStore` itself stays storage-only — the audit lives in the composing
  layers, as with the rest of it.

## Password reset

`auth.PasswordResetService(store, audit_log=…)` — audited facade, same split
as `MfaService` (`AuthStore` holds the `password_reset_tokens` table and
`create_reset_token` / `consume_reset_token`; the audit lives here).

```python
from auth import PasswordResetService

reset = PasswordResetService(store, audit_log=audit_log)

delivery = reset.request_reset("ada.ledger")   # -> ResetDelivery
delivery.reset_token   # raw, single-use, expiring (default 15 min)
delivery.reset_link
delivery.delivery_note # the blunt "never return this in an API response" warning

reset.redeem_reset(delivery.reset_token, "a-new-password")   # -> True / False
```

- **Enumeration-safe** — `request_reset` does the *same work* and returns
  the *same shape* (a real token in a `ResetDelivery`) whether or not the
  username exists. There is no existence branch and no PBKDF2-scale work on
  the request path, so this endpoint cannot be used to discover which
  usernames exist — the defence is constant work, not just a generic reply.
  A token minted for a nonexistent user simply can never be redeemed, and
  every redeem failure (`invalid` / `used` / `expired` / user-gone) is the
  same generic `False`.
- **Same token strength as everything else** — `secrets.token_urlsafe(32)`,
  stored only as its sha256, raw token returned once and never logged
  (events carry a `sha256[:12]` fingerprint).
- **Single-use** — redeeming burns the token (`consumed_at`); a second
  attempt is `False` / reason `used`.
- **Revokes every session** — a redeem calls
  `AuthStore.revoke_all_sessions_for_user`, so a reset (a strong "the old
  sessions may be compromised" signal) logs the user out everywhere. MFA
  enrollment is left intact.
- **Delivery** — `request_reset` returns the token/link directly. In
  production it returns *nothing*; a background job emails the link to the
  account's verified address. The direct return is the prototype's stand-in
  for that email — and, as `delivery_note` says, itself the thing you must
  never ship.
- **Audited** — `auth.password_reset.requested` (with an internal
  `user_exists` field), `.redeemed` (with `sessions_invalidated`),
  `.redeem_failed` (with `reason`). Never the raw token, never either
  password. `AuthStore.last_reset_requested_at(username)` is a durable hook
  for future rate-limiting.

## Not in this prototype

- direct audit-log writes from `AuthStore` — `platform/session`,
  `auth.MfaService`, and `auth.PasswordResetService` compose it into flows
  that log to the shared `audit_log.AuditLogStore`; `AuthStore` on its own
  stays storage-only
- password strength rules, rate limiting / lockout, token refresh,
  expired-session / expired-reset-token cleanup, MFA recovery codes,
  encrypted-at-rest TOTP secrets, a real email delivery channel, FastAPI
  routes

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
