"""Prototype multi-tenancy layer for the platform chassis.

Learning/prototype component, same status as platform/auth: intentionally
NOT imported by app.py or any agent yet. It provides:

- a Tenant model (unique tenant_id, display name, created_at)
- one-tenant-per-user membership, associating an auth.User with exactly
  one Tenant
- TenantScope, a capability object, plus a ScopedTable helper that makes
  the `WHERE tenant_id = ?` filter structurally unforgettable

`auth` is put on sys.path here (the same trick auth uses for `../approvals`)
so callers can associate real auth.User objects with tenants.
"""

import sys
from pathlib import Path

_auth_dir = Path(__file__).resolve().parent.parent.parent / "auth"
if str(_auth_dir) not in sys.path:
    sys.path.insert(0, str(_auth_dir))

from .models import Membership, Tenant, TenantScope  # noqa: E402
from .scoped import MissingTenantScope, ScopedTable  # noqa: E402
from .store import (  # noqa: E402
    AlreadyAssigned,
    NoMembership,
    TenancyStore,
    TenantExists,
    TenantNotFound,
)

__all__ = [
    "Membership",
    "Tenant",
    "TenantScope",
    "MissingTenantScope",
    "ScopedTable",
    "TenancyStore",
    "AlreadyAssigned",
    "NoMembership",
    "TenantExists",
    "TenantNotFound",
]
