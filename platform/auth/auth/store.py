"""SQLite-backed user + session store for the prototype auth layer.

Same shape as audit_log.AuditLogStore: construct with a db_path, the schema
is created on init, call close() when done. Unlike the audit log this store
is deliberately mutable (sessions come and go) and is NOT hash-chained — it
is operational state, not a system of record.

Security properties worth stating out loud:

- Passwords are stored only as PBKDF2 hashes (see auth.passwords).
- `verify_login` is enumeration-safe: a wrong password and an unknown
  username return the same False and do the same amount of hashing work, so
  neither timing nor return value reveals which usernames exist.
- Session tokens are generated with `secrets.token_urlsafe` (unguessable).
  Only the sha256 of a token is persisted; the raw token is returned once at
  login. A dump of the `sessions` table therefore cannot be replayed.
"""

import hashlib
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Union

from .models import Role, Session, User
from .passwords import DUMMY_HASH, hash_password, verify_password

DEFAULT_TTL_SECONDS = 3600
_TOKEN_BYTES = 32

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    username TEXT NOT NULL REFERENCES users(username),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
"""


class UserExists(Exception):
    """Raised by create_user when the username is already registered."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class AuthStore:
    def __init__(self, db_path: Union[str, Path]):
        self.db_path = str(db_path)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # -- users -----------------------------------------------------------

    def create_user(
        self,
        username: str,
        password: str,
        role: Role,
        *,
        now: Optional[datetime] = None,
    ) -> User:
        created_at = (now or _utcnow()).isoformat()
        password_hash = hash_password(password)
        try:
            self._conn.execute(
                "INSERT INTO users (username, password_hash, role, created_at) "
                "VALUES (?, ?, ?, ?)",
                (username, password_hash, role.value, created_at),
            )
        except sqlite3.IntegrityError:
            raise UserExists(f"username {username!r} is already registered")
        self._conn.commit()
        return User(
            username=username,
            password_hash=password_hash,
            role=role,
            created_at=created_at,
        )

    def get_user(self, username: str) -> Optional[User]:
        row = self._conn.execute(
            "SELECT username, password_hash, role, created_at FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        if row is None:
            return None
        return User(
            username=row[0],
            password_hash=row[1],
            role=Role(row[2]),
            created_at=row[3],
        )

    # -- login ---------------------------------------------------------

    def verify_login(self, username: str, password: str) -> bool:
        """Return True iff username exists and password matches.

        On any failure returns False. An unknown username still runs one
        full PBKDF2 verification (against DUMMY_HASH) so that the unknown
        -username and wrong-password paths are indistinguishable.
        """
        user = self.get_user(username)
        if user is None:
            verify_password(password, DUMMY_HASH)
            return False
        return verify_password(password, user.password_hash)

    def login(
        self,
        username: str,
        password: str,
        *,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        now: Optional[datetime] = None,
    ) -> Optional[str]:
        """Verify credentials and, on success, issue a session token.

        Returns the raw token string, or None on any authentication
        failure (the caller cannot tell why it failed).
        """
        if not self.verify_login(username, password):
            return None

        issued = now or _utcnow()
        expires = issued + timedelta(seconds=ttl_seconds)
        token = _new_token()
        self._conn.execute(
            "INSERT INTO sessions (token_hash, username, created_at, expires_at) "
            "VALUES (?, ?, ?, ?)",
            (_token_hash(token), username, issued.isoformat(), expires.isoformat()),
        )
        self._conn.commit()
        return token

    # -- sessions ----------------------------------------------------

    def validate_token(
        self,
        token: str,
        *,
        now: Optional[datetime] = None,
    ) -> Optional[User]:
        """Return the User a token belongs to, or None if the token is
        unknown, malformed, or expired."""
        row = self._conn.execute(
            "SELECT username, expires_at FROM sessions WHERE token_hash = ?",
            (_token_hash(token),),
        ).fetchone()
        if row is None:
            return None

        username, expires_at = row
        if _parse(expires_at) <= (now or _utcnow()):
            return None
        return self.get_user(username)

    def logout(self, token: str) -> None:
        """Invalidate a session token. No-op if it does not exist."""
        self._conn.execute(
            "DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),)
        )
        self._conn.commit()

    def get_session(self, token: str) -> Optional[Session]:
        row = self._conn.execute(
            "SELECT token_hash, username, created_at, expires_at FROM sessions "
            "WHERE token_hash = ?",
            (_token_hash(token),),
        ).fetchone()
        if row is None:
            return None
        return Session(token_hash=row[0], username=row[1], created_at=row[2], expires_at=row[3])


def _new_token() -> str:
    return secrets.token_urlsafe(_TOKEN_BYTES)


def _parse(iso: str) -> datetime:
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
