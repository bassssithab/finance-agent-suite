"""Prototype authentication/login layer for the platform chassis.

This module is a learning/prototype component. It is intentionally NOT
imported by app.py or any agent yet. It provides:

- a User model with a securely-hashed password and a chassis Role
- a SQLite-backed user + session store (same pattern as audit_log)
- enumeration-safe login verification
- random session tokens whose raw value is never stored at rest
- optional TOTP multi-factor auth (auth.totp + auth.mfa.MfaService)
- enumeration-safe password reset (auth.reset.PasswordResetService)

Roles are reused from platform/approvals so the whole chassis has one
Role source of truth. `../approvals` and `../audit-log` are put on sys.path
here so this module is self-contained for the test run.
"""

import sys
from pathlib import Path

_platform = Path(__file__).resolve().parent.parent.parent
for _dep in ("approvals", "audit-log"):
    _p = str(_platform / _dep)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from approvals import Role  # noqa: E402

from . import totp  # noqa: E402
from .mfa import (  # noqa: E402
    MfaAlreadyEnabled,
    MfaError,
    MfaNotEnabled,
    MfaService,
    UnknownUser,
)
from .models import MfaEnrollment, ResetDelivery, Session, User  # noqa: E402
from .passwords import hash_password, verify_password  # noqa: E402
from .reset import PasswordResetService  # noqa: E402
from .store import AuthStore, UserExists  # noqa: E402

__all__ = [
    "Role",
    "Session",
    "User",
    "MfaEnrollment",
    "ResetDelivery",
    "hash_password",
    "verify_password",
    "AuthStore",
    "UserExists",
    "totp",
    "MfaService",
    "MfaError",
    "UnknownUser",
    "MfaAlreadyEnabled",
    "MfaNotEnabled",
    "PasswordResetService",
]
