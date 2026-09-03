"""Prototype Telegram-chat <-> auth.User linking layer.

Learning/prototype component, same status as platform/auth, platform/tenancy,
platform/session: NOT wired into app.py or any agent, and there is no real
Telegram bot here yet — this module is only the linking state machine and
its audit trail. Fictional users and chat_ids only.

Lifecycle:

    generate_link_code(user)   -> a short, unguessable, single-use code that
                                  expires after a configurable window
    redeem_link_code(code, chat_id)
                               -> permanently links chat_id -> user, marks
                                  the code consumed; a reused / expired /
                                  unknown code fails loudly
    resolve_chat_id(chat_id)   -> the auth.User currently linked, or None
    revoke_link(chat_id, revoked_by=...)
                               -> chat_id becomes unresolvable until re-linked

Every linking action — code generated, code redeemed, link revoked, and
every FAILED redemption attempt — is written to the injected
audit_log.AuditLogStore. resolve_chat_id is the one exception: it runs on
every inbound message, so logging it would swamp the chain.

`../auth` and `../audit-log` are put on sys.path here (the same trick the
other platform modules use).
"""

import sys
from pathlib import Path

_platform = Path(__file__).resolve().parent.parent.parent
for _dep in ("auth", "audit-log"):
    _p = str(_platform / _dep)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from .codes import looks_like_code  # noqa: E402
from .models import TelegramLink  # noqa: E402
from .store import (  # noqa: E402
    ChatAlreadyLinked,
    InvalidLinkCode,
    LinkCodeAlreadyUsed,
    LinkCodeExpired,
    NoActiveLink,
    TelegramLinkError,
    TelegramLinkStore,
    UnknownUser,
)

__all__ = [
    "TelegramLink",
    "TelegramLinkStore",
    "TelegramLinkError",
    "UnknownUser",
    "InvalidLinkCode",
    "LinkCodeAlreadyUsed",
    "LinkCodeExpired",
    "ChatAlreadyLinked",
    "NoActiveLink",
    "looks_like_code",
]
