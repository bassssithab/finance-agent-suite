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

from . import totp
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

CREATE TABLE IF NOT EXISTS mfa_enrollments (
    username TEXT PRIMARY KEY REFERENCES users(username),
    secret TEXT NOT NULL,          -- base32; recoverable by necessity (TOTP)
    enabled_at TEXT NOT NULL,
    last_used_step INTEGER          -- the last time-step burned; NULL until first use
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
        totp_code: Optional[str] = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        now: Optional[datetime] = None,
    ) -> Optional[str]:
        """Verify credentials (and MFA, if enabled) and, on success, issue a
        session token.

        Returns the raw token string, or None on any authentication failure —
        wrong password, or (for an MFA account) a missing / invalid / reused
        TOTP code. The caller cannot tell which from the return value;
        SessionService.authenticate distinguishes them for its audit trail.
        """
        if not self.verify_login(username, password):
            return None

        if self.mfa_enabled(username):
            if totp_code is None:
                return None
            if self.consume_totp(username, totp_code, now=now) != "ok":
                return None

        return self.start_session(username, ttl_seconds=ttl_seconds, now=now)

    def start_session(
        self,
        username: str,
        *,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        now: Optional[datetime] = None,
    ) -> str:
        """Issue a session token for an ALREADY-authenticated user.

        No password or MFA check — the caller is responsible for having done
        both. SessionService uses this after verifying each factor itself so
        it can report a distinct reason for every failure.
        """
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

    # -- MFA (TOTP) --------------------------------------------------
    #
    # Storage + the one atomic verify-and-burn. The audited lifecycle
    # (enable / disable / standalone verify) lives in auth.mfa.MfaService;
    # this store stays audit-free, like the rest of it.

    def set_mfa_secret(
        self, username: str, secret: str, *, now: Optional[datetime] = None
    ) -> None:
        self._conn.execute(
            "INSERT INTO mfa_enrollments (username, secret, enabled_at, last_used_step) "
            "VALUES (?, ?, ?, NULL)",
            (username, secret, (now or _utcnow()).isoformat()),
        )
        self._conn.commit()

    def get_mfa_secret(self, username: str) -> Optional[str]:
        row = self._conn.execute(
            "SELECT secret FROM mfa_enrollments WHERE username = ?", (username,)
        ).fetchone()
        return row[0] if row is not None else None

    def clear_mfa_secret(self, username: str) -> None:
        self._conn.execute(
            "DELETE FROM mfa_enrollments WHERE username = ?", (username,)
        )
        self._conn.commit()

    def mfa_enabled(self, username: str) -> bool:
        return self.get_mfa_secret(username) is not None

    def consume_totp(
        self, username: str, code: str, *, now: Optional[datetime] = None
    ) -> str:
        """Verify a TOTP code and, on success, BURN its time-step so it can
        never be replayed. Returns one of:

        - "ok"           valid, and not seen before — step recorded
        - "invalid"      no time-step in the drift window matches
        - "reused"       the matched step was already spent (replay, or an
                         older still-in-window code after a newer one)
        - "not_enrolled" the user has no MFA secret

        Shared by login() and MfaService.verify() so a code spent through one
        path cannot be replayed through the other.
        """
        secret = self.get_mfa_secret(username)
        if secret is None:
            return "not_enrolled"

        ts = (now or _utcnow()).timestamp()
        step = totp.matching_step(secret, code, timestamp=ts)
        if step is None:
            return "invalid"

        row = self._conn.execute(
            "SELECT last_used_step FROM mfa_enrollments WHERE username = ?", (username,)
        ).fetchone()
        last_used = row[0] if row is not None else None
        if last_used is not None and step <= last_used:
            return "reused"

        self._conn.execute(
            "UPDATE mfa_enrollments SET last_used_step = ? WHERE username = ?",
            (step, username),
        )
        self._conn.commit()
        return "ok"

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
