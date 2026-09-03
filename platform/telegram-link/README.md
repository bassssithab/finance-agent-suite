# platform/telegram-link

> **Prototype / learning module.** Not wired into `app.py` or any agent, and
> **there is no real Telegram bot here** — wiring an actual bot / webhook /
> message handlers is a separate future step. This module is only the
> linking state machine and its audit trail. Fictional users and chat_ids
> only.

Securely associates a Telegram `chat_id` with an existing `auth.User`, via
a one-time code the user redeems.

## Lifecycle

```python
from audit_log import AuditLogStore
from auth import AuthStore
from telegram_link import TelegramLinkStore

store = TelegramLinkStore(
    "telegram_link.db",
    audit_log=AuditLogStore("audit.db"),
    auth_store=AuthStore("auth.db"),
)

code = store.generate_link_code("dana.acme")       # -> "xK3p_2Qz9Ab-"  (returned once)
link = store.redeem_link_code(code, chat_id=700001)  # permanently links the two
user = store.resolve_chat_id(700001)                # -> auth.User, or None
store.revoke_link(700001, revoked_by="admin.root")  # chat_id now resolves to None
```

## Guarantees

- **Codes are unguessable** — `secrets.token_urlsafe`, 12 chars (~72 bits),
  not sequential, not time-derived. `looks_like_code(text)` reports whether
  a string is shaped like one (a routing hint for a chat bot — not
  validation).
- **Codes are single-use** — consumed on redemption; a second redemption
  raises `LinkCodeAlreadyUsed`. Generating a new code for a user drops that
  user's previous unused one (one live code per user).
- **Codes expire** — after `ttl_seconds` (default 600); redeeming later
  raises `LinkCodeExpired`. Expiry is checked in Python against an
  injectable `now`.
- **Codes are hashed at rest** — only `sha256(code)` is stored; a dump of
  `link_codes` cannot be redeemed. The raw code is returned once and never
  logged (events carry a `sha256[:12]` fingerprint).
- **One user per chat_id** — re-linking an actively-linked `chat_id` raises
  `ChatAlreadyLinked`; an admin must `revoke_link` first. A *revoked* link
  can be replaced by redeeming a new code.
- **Revocation is immediate** — `resolve_chat_id` returns `None` the moment
  a link is revoked, until a new code is redeemed.
- **Everything is audited** — `code_generated`, `code_redeemed`,
  `link_revoked`, and every **failed** redemption
  (`redeem_failed`, with a `reason`) is written to the injected
  `audit_log.AuditLogStore`. Failed redemptions are a real security signal.
  `resolve_chat_id` is the one action *not* audited — it runs on every
  inbound message.

## Not in this prototype

- the actual Telegram bot: webhook, `getUpdates`, message handlers, the
  `/start <code>` command that would call `redeem_link_code`
- rate-limiting code generation; per-chat lockout after repeated failed
  redemptions
- linking one user to multiple chats (only chat → user uniqueness is
  enforced; user → chat is currently many-to-one)
- adding this module to `docs/ARCHITECTURE.md`'s "Identity & Access
  (prototype)" section — a small follow-up

## Development

```bash
cd platform/telegram-link && ../../.venv/bin/pytest -v
```

`conftest.py` puts `telegram_link/`, `../auth`, and `../audit-log` on
`sys.path` for the test run — no install step.
