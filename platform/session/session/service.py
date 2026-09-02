"""SessionService — one call to authenticate a user and place them in their tenant.

This module owns no storage. It orchestrates two existing stores:

- auth.AuthStore     — verifies credentials, issues/validates session tokens
- tenancy.TenancyStore — resolves a user's one tenant membership to a scope

The caller constructs and closes those stores; SessionService just holds
references to them, so there is no close() here.
"""

import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

_platform = Path(__file__).resolve().parent.parent.parent
for _dep in ("auth", "tenancy"):
    _p = str(_platform / _dep)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from auth import AuthStore  # noqa: E402
from tenancy import NoMembership, TenancyStore  # noqa: E402

from .models import AuthenticatedSession, AuthFailure  # noqa: E402

Result = Union[AuthenticatedSession, AuthFailure]


class SessionService:
    def __init__(self, auth_store: AuthStore, tenancy_store: TenancyStore):
        self.auth_store = auth_store
        self.tenancy_store = tenancy_store

    def authenticate(
        self,
        username: str,
        password: str,
        *,
        ttl_seconds: Optional[int] = None,
        now: Optional[datetime] = None,
    ) -> Result:
        """Verify credentials, then resolve the user's tenant.

        Returns an AuthenticatedSession on full success, or:
        - AuthFailure.BAD_CREDENTIALS      — wrong username or password
        - AuthFailure.NO_TENANT_ASSIGNED   — login ok, but no tenant yet
          (the issued token is rolled back in this case)
        """
        login_kwargs = {"now": now}
        if ttl_seconds is not None:
            login_kwargs["ttl_seconds"] = ttl_seconds
        token = self.auth_store.login(username, password, **login_kwargs)
        if token is None:
            return AuthFailure.BAD_CREDENTIALS

        user = self.auth_store.validate_token(token, now=now)
        if user is None:
            # Should not happen (token was just issued); treat defensively.
            return AuthFailure.BAD_CREDENTIALS

        try:
            scope = self.tenancy_store.scope_for_user(user)
        except NoMembership:
            # Roll back the just-issued token: an unassigned user must not
            # walk away with a usable session.
            self.auth_store.logout(token)
            return AuthFailure.NO_TENANT_ASSIGNED

        return AuthenticatedSession(token=token, user=user, scope=scope)

    def validate(
        self,
        token: str,
        *,
        now: Optional[datetime] = None,
    ) -> Result:
        """Re-derive the AuthenticatedSession bundle from a token alone.

        Returns an AuthenticatedSession, or:
        - AuthFailure.INVALID_TOKEN        — unknown / malformed / expired / logged out
        - AuthFailure.NO_TENANT_ASSIGNED   — live token, but the user has no
          tenant (e.g. assigned to an org only after the token was issued);
          the token is left intact here — it is genuinely valid.
        """
        user = self.auth_store.validate_token(token, now=now)
        if user is None:
            return AuthFailure.INVALID_TOKEN

        try:
            scope = self.tenancy_store.scope_for_user(user)
        except NoMembership:
            return AuthFailure.NO_TENANT_ASSIGNED

        return AuthenticatedSession(token=token, user=user, scope=scope)
