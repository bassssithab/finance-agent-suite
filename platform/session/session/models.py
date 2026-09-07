"""Data model for the prototype combined auth + tenancy flow.

See docs/ARCHITECTURE.md. Reuses auth.User and tenancy.TenantScope rather
than redefining either.
"""

import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

_platform = Path(__file__).resolve().parent.parent.parent
for _dep in ("auth", "tenancy"):
    _p = str(_platform / _dep)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from auth import User  # noqa: E402
from tenancy import TenantScope  # noqa: E402

__all__ = ["AuthenticatedSession", "AuthFailure"]


@dataclass(frozen=True)
class AuthenticatedSession:
    """Everything needed to act as one user within their tenant, from one call.

    `token` is the auth session token — hold onto it and pass it to
    SessionService.validate() on a later request to re-derive this same
    bundle without the password.
    """

    token: str
    user: User
    scope: TenantScope

    @property
    def tenant_id(self) -> str:
        return self.scope.tenant_id


class AuthFailure(str, Enum):
    """Why authenticate() / validate() did not return a session.

    Every non-success outcome is one of these distinct, self-describing
    values — never a bare None, never a session with a missing scope.
    """

    BAD_CREDENTIALS = "bad_credentials"
    """Wrong username or password. Deliberately does not say which — the
    auth layer's enumeration-safety is preserved."""

    MFA_REQUIRED = "mfa_required"
    """The password was correct, but the account has TOTP MFA enabled and no
    code was supplied. Re-call authenticate() with `totp_code=`."""

    MFA_INVALID = "mfa_invalid"
    """The password was correct, but the supplied TOTP code was wrong,
    outside the drift window, or a replay of one already used. (Which of
    those is recorded in the audit trail, not exposed here.)"""

    NO_TENANT_ASSIGNED = "no_tenant_assigned"
    """The password (and MFA, if any) checked out, but the user does not
    belong to any tenant yet (a real, expected state for a freshly-created
    user). No session token is issued."""

    INVALID_TOKEN = "invalid_token"
    """validate() only: the token is unknown, malformed, expired, or has
    been logged out."""
