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

__all__ = ["Role", "User", "Session", "MfaEnrollment", "ResetDelivery"]


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


@dataclass(frozen=True)
class MfaEnrollment:
    """The result of MfaService.enable(): everything needed to add the account
    to a real authenticator app.

    Unlike a password (hashed) or a session token (hashed), the TOTP `secret`
    must be stored recoverable — verification needs the actual shared secret.
    It is returned here once and never again from the API, and never written
    to the audit log. `provisioning_uri` embeds the secret; treat it the same.
    """

    username: str
    secret: str            # base32 — for manual entry
    provisioning_uri: str  # otpauth://... — for a QR code
    enabled_at: str        # ISO-8601 UTC


@dataclass(frozen=True)
class ResetDelivery:
    """What request_reset() hands back.

    In production this method returns NOTHING — a background job emails the
    link to the account's verified address. The prototype returns the token
    directly so local dev and tests can finish the flow; `delivery_note`
    spells that out. Returned identically (same shape, real token) whether or
    not the username exists, so the request endpoint cannot be used to
    enumerate accounts.
    """

    username: str
    reset_token: str       # raw — treat as a credential; never persisted, never logged
    reset_link: str
    requested_at: str      # ISO-8601 UTC
    expires_at: str        # ISO-8601 UTC
    delivery_note: str
