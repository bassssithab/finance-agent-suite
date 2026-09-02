"""Prototype authentication/login layer for the platform chassis.

This module is a learning/prototype component. It is intentionally NOT
imported by app.py or any agent yet. It provides:

- a User model with a securely-hashed password and a chassis Role
- a SQLite-backed user + session store (same pattern as audit_log)
- enumeration-safe login verification
- random session tokens whose raw value is never stored at rest

Roles are reused from platform/approvals so the whole chassis has one
Role source of truth. `approvals/__init__.py` in turn puts `../audit-log`
on sys.path, so importing it here is self-contained for the test run.
"""

import sys
from pathlib import Path

_approvals_dir = Path(__file__).resolve().parent.parent.parent / "approvals"
if str(_approvals_dir) not in sys.path:
    sys.path.insert(0, str(_approvals_dir))

from approvals import Role  # noqa: E402

from .models import Session, User  # noqa: E402
from .passwords import hash_password, verify_password  # noqa: E402
from .store import AuthStore, UserExists  # noqa: E402

__all__ = [
    "Role",
    "Session",
    "User",
    "hash_password",
    "verify_password",
    "AuthStore",
    "UserExists",
]
