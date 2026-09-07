"""The audited TOTP-MFA lifecycle: enable / disable / verify.

A thin facade over AuthStore's MFA storage + auth.totp + the provisioning
URI. AuthStore stays storage-only (its README's deliberate choice); this is
where the AuditLogStore is injected, the same required way as every other
infrastructure module.

Login-time MFA enforcement is in AuthStore.login / SessionService.authenticate
— this class is what a settings UI calls to turn MFA on and off.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_audit_log_dir = Path(__file__).resolve().parent.parent.parent / "audit-log"
if str(_audit_log_dir) not in sys.path:
    sys.path.insert(0, str(_audit_log_dir))

from audit_log import AuditEvent, AuditLogStore  # noqa: E402

from . import totp
from .models import MfaEnrollment
from .store import AuthStore

_AGENT = "platform/auth"
DEFAULT_ISSUER = "finance-agent-suite"


class MfaError(Exception):
    """Base class for MFA lifecycle failures."""


class UnknownUser(MfaError):
    """enable() was asked for a username auth does not know."""


class MfaAlreadyEnabled(MfaError):
    """enable() was called for a user who already has MFA. Disable first."""


class MfaNotEnabled(MfaError):
    """disable() / verify() was called for a user without MFA enabled."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _username_of(user_or_username) -> str:
    return getattr(user_or_username, "username", user_or_username)


class MfaService:
    def __init__(self, auth_store: AuthStore, *, audit_log: AuditLogStore):
        self.auth_store = auth_store
        self.audit_log = audit_log

    def _audit(self, action, actor, *, inputs, output=None, now=None) -> None:
        self.audit_log.append(AuditEvent(
            timestamp=(now or _utcnow()).isoformat(),
            agent=_AGENT,
            action=action,
            actor=actor,
            inputs=inputs,
            output=output,
        ))

    def is_enabled(self, username: str) -> bool:
        return self.auth_store.mfa_enabled(_username_of(username))

    def enable(
        self,
        user_or_username,
        *,
        issuer: str = DEFAULT_ISSUER,
        now: Optional[datetime] = None,
    ) -> MfaEnrollment:
        """Generate a real TOTP secret for an existing user and store it.

        Returns an MfaEnrollment (base32 secret + otpauth:// URI) — the only
        time the secret leaves the API. Raises UnknownUser or
        MfaAlreadyEnabled. The audit event carries neither the secret nor
        the URI.
        """
        username = _username_of(user_or_username)
        if self.auth_store.get_user(username) is None:
            raise UnknownUser(f"no auth user named {username!r}")
        if self.auth_store.mfa_enabled(username):
            raise MfaAlreadyEnabled(f"MFA is already enabled for {username!r}")

        stamp = now or _utcnow()
        secret = totp.generate_secret()
        self.auth_store.set_mfa_secret(username, secret, now=stamp)
        uri = totp.provisioning_uri(secret, account_name=username, issuer=issuer)

        self._audit(
            "auth.mfa.enabled", username,
            inputs={"username": username, "issuer": issuer},
            now=stamp,
        )
        return MfaEnrollment(
            username=username, secret=secret, provisioning_uri=uri,
            enabled_at=stamp.isoformat(),
        )

    def disable(self, user_or_username, *, now: Optional[datetime] = None) -> None:
        """Turn MFA off. Raises MfaNotEnabled if it was not on."""
        username = _username_of(user_or_username)
        if not self.auth_store.mfa_enabled(username):
            raise MfaNotEnabled(f"MFA is not enabled for {username!r}")
        self.auth_store.clear_mfa_secret(username)
        self._audit("auth.mfa.disabled", username, inputs={"username": username}, now=now)

    def verify(self, user_or_username, code: str, *, now: Optional[datetime] = None) -> bool:
        """Check a submitted 6-digit code, burning its time-step on success.

        Returns True/False. Raises MfaNotEnabled if the user has no MFA.
        A reused code returns False, audited with reason "reused" — the
        security signal that someone is replaying an intercepted code.
        """
        username = _username_of(user_or_username)
        if not self.auth_store.mfa_enabled(username):
            raise MfaNotEnabled(f"MFA is not enabled for {username!r}")

        status = self.auth_store.consume_totp(username, code, now=now)
        if status == "ok":
            self._audit("auth.mfa.verify_succeeded", username,
                        inputs={"username": username}, now=now)
            return True
        self._audit("auth.mfa.verify_failed", username,
                    inputs={"username": username, "reason": status}, now=now)
        return False
