"""Prototype combined authentication + tenancy flow for the platform chassis.

Learning/prototype component, same status as platform/auth and
platform/tenancy: intentionally NOT imported by app.py or any agent yet.

It composes an existing auth.AuthStore, tenancy.TenancyStore, and
audit_log.AuditLogStore into one call:

    svc = SessionService(auth_store, tenancy_store, audit_log)
    result = svc.authenticate(username, password)
    # -> AuthenticatedSession (token + User + ready-to-use TenantScope)
    # -> AuthFailure.BAD_CREDENTIALS / AuthFailure.NO_TENANT_ASSIGNED
    # ... and one tamper-evident audit event per authenticate/validate/logout

`../auth`, `../tenancy`, and `../audit-log` are put on sys.path here (the
same trick those modules use for their own dependencies).
"""

import sys
from pathlib import Path

_platform = Path(__file__).resolve().parent.parent.parent
for _dep in ("auth", "tenancy", "audit-log"):
    _p = str(_platform / _dep)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from .models import AuthenticatedSession, AuthFailure  # noqa: E402
from .service import SessionService  # noqa: E402

__all__ = [
    "AuthenticatedSession",
    "AuthFailure",
    "SessionService",
]
