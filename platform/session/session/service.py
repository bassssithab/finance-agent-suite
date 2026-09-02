"""SessionService — one call to authenticate a user and place them in their tenant.

This module owns no storage. It orchestrates three existing stores:

- auth.AuthStore        — verifies credentials, issues/validates session tokens
- tenancy.TenancyStore  — resolves a user's one tenant membership to a scope
- audit_log.AuditLogStore — the hash-chained, tamper-evident activity log the
  agents use; every authenticate / validate / logout writes one event to it

The caller constructs and closes those stores; SessionService just holds
references to them, so there is no close() here.

Activity logging notes:
- The password is NEVER written to the audit log, in any field.
- The raw session token is never written either — only a short fingerprint,
  `sha256(token)[:12]`, so events for one session can be correlated without
  the log carrying a replayable credential into an exported evidence pack.
- Each authenticate / validate call emits exactly one event. The internal
  token rollback on NO_TENANT_ASSIGNED calls auth_store.logout() directly,
  so it does not also emit a session.logout event — the
  session.login.failed.no_tenant event is the record.
"""

import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Union

_platform = Path(__file__).resolve().parent.parent.parent
for _dep in ("auth", "tenancy", "audit-log"):
    _p = str(_platform / _dep)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from audit_log import AuditEvent, AuditLogStore  # noqa: E402
from auth import AuthStore  # noqa: E402
from tenancy import NoMembership, TenancyStore  # noqa: E402

from .models import AuthenticatedSession, AuthFailure  # noqa: E402

Result = Union[AuthenticatedSession, AuthFailure]

_AGENT = "platform/session"


def _token_fingerprint(token: str) -> str:
    """A short, non-reversible tag for correlating a session's events.

    Not the token. Never enough to authenticate with.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


class SessionService:
    def __init__(
        self,
        auth_store: AuthStore,
        tenancy_store: TenancyStore,
        audit_log: AuditLogStore,
    ):
        self.auth_store = auth_store
        self.tenancy_store = tenancy_store
        self.audit_log = audit_log

    def _audit(
        self,
        action: str,
        actor: str,
        *,
        inputs: Optional[dict] = None,
        output: Any = None,
        now: Optional[datetime] = None,
    ) -> None:
        self.audit_log.append(AuditEvent(
            timestamp=(now or datetime.now(timezone.utc)).isoformat(),
            agent=_AGENT,
            action=action,
            actor=actor,
            inputs=inputs or {},
            output=output,
        ))

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

        Emits one of session.login.succeeded / .failed.bad_credentials /
        .failed.no_tenant. The password is never logged.
        """
        base_inputs = {"username": username, "ttl_seconds": ttl_seconds}

        login_kwargs = {"now": now}
        if ttl_seconds is not None:
            login_kwargs["ttl_seconds"] = ttl_seconds
        token = self.auth_store.login(username, password, **login_kwargs)

        if token is None:
            self._audit(
                "session.login.failed.bad_credentials", username,
                inputs=base_inputs,
                output={"reason": AuthFailure.BAD_CREDENTIALS.value}, now=now,
            )
            return AuthFailure.BAD_CREDENTIALS

        user = self.auth_store.validate_token(token, now=now)
        if user is None:
            # Should not happen (token was just issued); treat defensively.
            self._audit(
                "session.login.failed.bad_credentials", username,
                inputs=base_inputs,
                output={"reason": AuthFailure.BAD_CREDENTIALS.value}, now=now,
            )
            return AuthFailure.BAD_CREDENTIALS

        try:
            scope = self.tenancy_store.scope_for_user(user)
        except NoMembership:
            # Roll back the just-issued token: an unassigned user must not
            # walk away with a usable session. Direct call — no logout event.
            self.auth_store.logout(token)
            self._audit(
                "session.login.failed.no_tenant", username,
                inputs=base_inputs,
                output={"reason": AuthFailure.NO_TENANT_ASSIGNED.value}, now=now,
            )
            return AuthFailure.NO_TENANT_ASSIGNED

        self._audit(
            "session.login.succeeded", user.username,
            inputs=base_inputs,
            output={
                "tenant_id": scope.tenant_id,
                "role": user.role.value,
                "token_fingerprint": _token_fingerprint(token),
            },
            now=now,
        )
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

        Emits one of session.validate.succeeded / .failed.invalid_token /
        .failed.no_tenant. Only the token fingerprint is logged.
        """
        fingerprint = _token_fingerprint(token)

        user = self.auth_store.validate_token(token, now=now)
        if user is None:
            self._audit(
                "session.validate.failed.invalid_token", "unknown",
                inputs={"token_fingerprint": fingerprint},
                output={"reason": AuthFailure.INVALID_TOKEN.value}, now=now,
            )
            return AuthFailure.INVALID_TOKEN

        try:
            scope = self.tenancy_store.scope_for_user(user)
        except NoMembership:
            self._audit(
                "session.validate.failed.no_tenant", user.username,
                inputs={"token_fingerprint": fingerprint},
                output={"reason": AuthFailure.NO_TENANT_ASSIGNED.value}, now=now,
            )
            return AuthFailure.NO_TENANT_ASSIGNED

        self._audit(
            "session.validate.succeeded", user.username,
            inputs={"token_fingerprint": fingerprint},
            output={"username": user.username, "tenant_id": scope.tenant_id},
            now=now,
        )
        return AuthenticatedSession(token=token, user=user, scope=scope)

    def logout(self, token: str, *, now: Optional[datetime] = None) -> None:
        """End a session and emit a session.logout event.

        Resolves the token's user first (best-effort — an already-expired
        token still logs, with actor "unknown"), then invalidates it.
        """
        user = self.auth_store.validate_token(token, now=now)
        actor = user.username if user is not None else "unknown"
        self.auth_store.logout(token)
        self._audit(
            "session.logout", actor,
            inputs={"token_fingerprint": _token_fingerprint(token)},
            now=now,
        )
