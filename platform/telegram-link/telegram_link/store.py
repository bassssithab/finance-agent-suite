"""SQLite-backed store for the prototype Telegram linking layer.

Same shape as auth.AuthStore: construct with a db_path, schema created on
init, call close() when done. Composes two injected stores, both required:

- audit_log.AuditLogStore  — every linking action (and every failed
  redemption) is appended here, the hash-chained tamper-evident log
- auth.AuthStore           — generate validates the user exists; resolve
  returns a real auth.User

Security properties:

- Linking codes are stored only as SHA-256 hashes (see codes.py). The raw
  code is returned once from generate_link_code and never persisted.
- Code expiry is checked in Python against an injectable `now`, not in SQL.
- A chat_id can be actively linked to at most one user. Re-linking an
  already-linked chat_id fails loudly (ChatAlreadyLinked) — an admin must
  revoke_link first. A *revoked* link can be replaced by redeeming a new
  code.
- Failed redemptions are a real security signal, so they are audited
  (telegram_link.redeem_failed) before the exception is raised.
"""

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Union

from audit_log import AuditEvent, AuditLogStore
from auth import AuthStore, User

from . import codes
from .models import TelegramLink

_AGENT = "platform/telegram-link"
DEFAULT_TTL_SECONDS = 600  # 10 minutes

_SCHEMA = """
CREATE TABLE IF NOT EXISTS link_codes (
    code_hash TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    consumed_at TEXT,
    consumed_chat_id INTEGER
);

CREATE TABLE IF NOT EXISTS chat_links (
    chat_id INTEGER PRIMARY KEY,
    username TEXT NOT NULL,
    linked_at TEXT NOT NULL,
    linked_via TEXT NOT NULL,
    revoked_at TEXT,
    revoked_by TEXT
);
"""


class TelegramLinkError(Exception):
    """Base class for every loud failure in this module."""


class UnknownUser(TelegramLinkError):
    """generate_link_code was asked for a username auth does not know."""


class InvalidLinkCode(TelegramLinkError):
    """redeem_link_code was given a code that does not exist."""


class LinkCodeAlreadyUsed(TelegramLinkError):
    """redeem_link_code was given a code that has already been consumed."""


class LinkCodeExpired(TelegramLinkError):
    """redeem_link_code was given a code past its expiry window."""


class ChatAlreadyLinked(TelegramLinkError):
    """redeem_link_code was called for a chat_id that already has an active
    link. Revoke the existing link first."""


class NoActiveLink(TelegramLinkError):
    """revoke_link was called for a chat_id with no active link."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse(iso: str) -> datetime:
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _username_of(user_or_username) -> str:
    """Accept an auth.User (anything with a .username) or a bare string."""
    return getattr(user_or_username, "username", user_or_username)


class TelegramLinkStore:
    def __init__(
        self,
        db_path: Union[str, Path],
        *,
        audit_log: AuditLogStore,
        auth_store: AuthStore,
    ):
        self.db_path = str(db_path)
        self.audit_log = audit_log
        self.auth_store = auth_store
        self._conn = sqlite3.connect(self.db_path)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # -- audit helper ------------------------------------------------

    def _audit(self, action, actor, *, inputs, output=None, now=None) -> None:
        self.audit_log.append(AuditEvent(
            timestamp=(now or _utcnow()).isoformat(),
            agent=_AGENT,
            action=action,
            actor=actor,
            inputs=inputs,
            output=output,
        ))

    # -- generate ---------------------------------------------------

    def generate_link_code(
        self,
        user_or_username,
        *,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        now: Optional[datetime] = None,
    ) -> str:
        """Create a one-time linking code for an existing user.

        Any earlier unconsumed code for the same user is dropped, so at most
        one code is live per user. Returns the raw code once; only its hash
        is stored. Raises UnknownUser if auth does not know the username.
        """
        username = _username_of(user_or_username)
        if self.auth_store.get_user(username) is None:
            raise UnknownUser(f"no auth user named {username!r}")

        issued = now or _utcnow()
        expires = issued + timedelta(seconds=ttl_seconds)

        self._conn.execute(
            "DELETE FROM link_codes WHERE username = ? AND consumed_at IS NULL",
            (username,),
        )
        code = codes.new_code()
        self._conn.execute(
            "INSERT INTO link_codes (code_hash, username, created_at, expires_at, "
            "consumed_at, consumed_chat_id) VALUES (?, ?, ?, ?, NULL, NULL)",
            (codes.code_hash(code), username, issued.isoformat(), expires.isoformat()),
        )
        self._conn.commit()

        self._audit(
            "telegram_link.code_generated", username,
            inputs={"username": username, "ttl_seconds": ttl_seconds},
            output={"code_fingerprint": codes.fingerprint(code),
                    "expires_at": expires.isoformat()},
            now=issued,
        )
        return code

    # -- redeem ---------------------------------------------------

    def redeem_link_code(
        self,
        code: str,
        chat_id: int,
        *,
        now: Optional[datetime] = None,
    ) -> TelegramLink:
        """Redeem a code for a chat_id, permanently linking the two.

        Loud failure (never a silent no-op) on:
        - InvalidLinkCode      the code does not exist
        - LinkCodeAlreadyUsed  the code was already consumed
        - LinkCodeExpired      the code is past its window
        - ChatAlreadyLinked    this chat_id already has an active link

        Every one of those is audited as telegram_link.redeem_failed before
        the exception is raised.
        """
        at = now or _utcnow()
        fp = codes.fingerprint(code)
        row = self._conn.execute(
            "SELECT username, expires_at, consumed_at FROM link_codes WHERE code_hash = ?",
            (codes.code_hash(code),),
        ).fetchone()

        if row is None:
            self._audit(
                "telegram_link.redeem_failed", "unknown",
                inputs={"chat_id": chat_id, "code_fingerprint": fp, "reason": "invalid"},
                now=at,
            )
            raise InvalidLinkCode("unknown linking code")

        username, expires_at, consumed_at = row

        if consumed_at is not None:
            self._audit(
                "telegram_link.redeem_failed", username,
                inputs={"chat_id": chat_id, "code_fingerprint": fp, "reason": "already_used"},
                now=at,
            )
            raise LinkCodeAlreadyUsed("this linking code has already been used")

        if _parse(expires_at) <= at:
            self._audit(
                "telegram_link.redeem_failed", username,
                inputs={"chat_id": chat_id, "code_fingerprint": fp, "reason": "expired"},
                now=at,
            )
            raise LinkCodeExpired("this linking code has expired")

        existing = self.get_link(chat_id)
        if existing is not None and existing.is_active:
            self._audit(
                "telegram_link.redeem_failed", username,
                inputs={"chat_id": chat_id, "code_fingerprint": fp,
                        "reason": "chat_already_linked"},
                now=at,
            )
            raise ChatAlreadyLinked(
                f"chat {chat_id} is already linked to {existing.username!r}; "
                "revoke that link before re-linking"
            )

        superseded = existing is not None  # a revoked link being replaced
        self._conn.execute(
            "UPDATE link_codes SET consumed_at = ?, consumed_chat_id = ? WHERE code_hash = ?",
            (at.isoformat(), chat_id, codes.code_hash(code)),
        )
        self._conn.execute(
            "INSERT OR REPLACE INTO chat_links (chat_id, username, linked_at, linked_via, "
            "revoked_at, revoked_by) VALUES (?, ?, ?, ?, NULL, NULL)",
            (chat_id, username, at.isoformat(), fp),
        )
        self._conn.commit()

        self._audit(
            "telegram_link.code_redeemed", username,
            inputs={"chat_id": chat_id, "code_fingerprint": fp},
            output={"linked_at": at.isoformat(), "superseded_revoked_link": superseded},
            now=at,
        )
        return TelegramLink(
            chat_id=chat_id, username=username, linked_at=at.isoformat(),
            linked_via=fp, revoked_at=None, revoked_by=None,
        )

    # -- resolve (NOT audited — runs per inbound message) -----------

    def resolve_chat_id(self, chat_id: int) -> Optional[User]:
        """The auth.User currently linked to this chat_id, or None.

        None when: no link, the link is revoked, or the linked user no
        longer exists in auth. Deliberately not written to the audit log —
        this is called on every inbound message.
        """
        row = self._conn.execute(
            "SELECT username FROM chat_links WHERE chat_id = ? AND revoked_at IS NULL",
            (chat_id,),
        ).fetchone()
        if row is None:
            return None
        return self.auth_store.get_user(row[0])

    def get_link(self, chat_id: int) -> Optional[TelegramLink]:
        """The raw link row for a chat_id, including a revoked one."""
        row = self._conn.execute(
            "SELECT chat_id, username, linked_at, linked_via, revoked_at, revoked_by "
            "FROM chat_links WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()
        if row is None:
            return None
        return TelegramLink(
            chat_id=row[0], username=row[1], linked_at=row[2], linked_via=row[3],
            revoked_at=row[4], revoked_by=row[5],
        )

    # -- revoke ---------------------------------------------------

    def revoke_link(
        self,
        chat_id: int,
        *,
        revoked_by: str,
        now: Optional[datetime] = None,
    ) -> TelegramLink:
        """Kill a chat_id's link. It becomes unresolvable until a new code
        is redeemed. `revoked_by` identifies the admin and is required.
        Raises NoActiveLink if there is nothing active to revoke.
        """
        at = now or _utcnow()
        existing = self.get_link(chat_id)
        if existing is None or not existing.is_active:
            raise NoActiveLink(f"chat {chat_id} has no active link to revoke")

        self._conn.execute(
            "UPDATE chat_links SET revoked_at = ?, revoked_by = ? WHERE chat_id = ?",
            (at.isoformat(), revoked_by, chat_id),
        )
        self._conn.commit()

        self._audit(
            "telegram_link.link_revoked", revoked_by,
            inputs={"chat_id": chat_id, "previous_username": existing.username},
            now=at,
        )
        return TelegramLink(
            chat_id=chat_id, username=existing.username, linked_at=existing.linked_at,
            linked_via=existing.linked_via, revoked_at=at.isoformat(), revoked_by=revoked_by,
        )
