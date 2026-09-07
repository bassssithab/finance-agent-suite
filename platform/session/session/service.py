"""SessionService — one call to authenticate a user and place them in their tenant.

This module owns no storage. It orchestrates three existing stores:

- auth.AuthStore        — verifies credentials, issues/validates session tokens
- tenancy.TenancyStore  — resolves a user's one tenant membership to a scope
- audit_log.AuditLogStore — the hash-chained, tamper-evident activity log the
  agents use; every authenticate / validate / logout writes one event to it

The caller constructs and closes those stores; SessionService just holds
references to them, so there is no close() here.

authenticate() checks each factor itself — password (auth.verify_login),
then TOTP MFA if enabled (auth.consume_totp), then tenant membership — and
issues the token last, via auth.start_session. Nothing to roll back if a
later step fails.

Activity logging notes:
- The password is NEVER written to the audit log, in any field. Neither is
  a TOTP code.
- The raw session token is never written either — only a short fingerprint,
  `sha256(token)[:12]`, so events for one session can be correlated without
  the log carrying a replayable credential into an exported evidence pack.
- Each authenticate / validate call emits exactly one event.
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
        totp_code: Optional[str] = None,
        ttl_seconds: Optional[int] = None,
        now: Optional[datetime] = None,
    ) -> Result:
        """Verify each factor, then resolve the user's tenant, then issue a token.

        Returns an AuthenticatedSession on full success, or:
        - AuthFailure.BAD_CREDENTIALS      — wrong username or password
        - AuthFailure.MFA_REQUIRED         — password ok, MFA on, no code given
        - AuthFailure.MFA_INVALID          — password ok, code wrong / expired / replayed
        - AuthFailure.NO_TENANT_ASSIGNED   — all factors ok, but no tenant yet

        Each factor is checked here (not delegated to auth_store.login) so a
        distinct reason is recorded for every failure. The token is issued
        last — nothing to roll back if a later step fails. The password is
        never logged; a TOTP code is never logged.
        """
        base_inputs = {"username": username, "ttl_seconds": ttl_seconds}

        if not self.auth_store.verify_login(username, password):
            self._audit(
                "session.login.failed.bad_credentials", username,
                inputs=base_inputs,
                output={"reason": AuthFailure.BAD_CREDENTIALS.value}, now=now,
            )
            return AuthFailure.BAD_CREDENTIALS

        mfa_on = self.auth_store.mfa_enabled(username)
        if mfa_on:
            if not totp_code:
                self._audit(
                    "session.login.failed.mfa_required", username,
                    inputs=base_inputs,
                    output={"reason": AuthFailure.MFA_REQUIRED.value}, now=now,
                )
                return AuthFailure.MFA_REQUIRED
            status = self.auth_store.consume_totp(username, totp_code, now=now)
            if status != "ok":
                self._audit(
                    "session.login.failed.mfa_invalid", username,
                    inputs=base_inputs,
                    output={"reason": AuthFailure.MFA_INVALID.value, "detail": status},
                    now=now,
                )
                return AuthFailure.MFA_INVALID

        user = self.auth_store.get_user(username)  # password already verified

        try:
            scope = self.tenancy_store.scope_for_user(user)
        except NoMembership:
            self._audit(
                "session.login.failed.no_tenant", username,
                inputs=base_inputs,
                output={"reason": AuthFailure.NO_TENANT_ASSIGNED.value}, now=now,
            )
            return AuthFailure.NO_TENANT_ASSIGNED

        session_kwargs = {"now": now}
        if ttl_seconds is not None:
            session_kwargs["ttl_seconds"] = ttl_seconds
        token = self.auth_store.start_session(username, **session_kwargs)

        self._audit(
            "session.login.succeeded", user.username,
            inputs=base_inputs,
            output={
                "tenant_id": scope.tenant_id,
                "role": user.role.value,
                "token_fingerprint": _token_fingerprint(token),
                "mfa_used": mfa_on,
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
