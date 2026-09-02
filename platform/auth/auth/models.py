"""Data model for the prototype auth layer.

Role is reused from platform/approvals rather than redefined, so the chassis
has a single Role enum (preparer / reviewer / approver). See
docs/ARCHITECTURE.md.
"""

import sys
from dataclasses import dataclass
from pathlib import Path

_approvals_dir = Path(__file__).resolve().parent.parent.parent / "approvals"
if str(_approvals_dir) not in sys.path:
    sys.path.insert(0, str(_approvals_dir))

from approvals import Role  # noqa: E402  (re-exported for convenience)

__all__ = ["Role", "User", "Session"]


@dataclass
class User:
    """A registered account.

    `password_hash` is the self-describing PBKDF2 string produced by
    auth.passwords.hash_password — never a plaintext password.
    """

    username: str
    password_hash: str
    role: Role
    created_at: str  # ISO-8601 UTC


@dataclass
class Session:
    """A logged-in session.

    Only `token_hash` (sha256 of the raw token) is ever persisted. The raw
    token is returned to the caller once, at login, and never stored — a
    leak of the sessions table cannot be replayed as a valid token.
    """

    token_hash: str
    username: str
    created_at: str   # ISO-8601 UTC
    expires_at: str   # ISO-8601 UTC
