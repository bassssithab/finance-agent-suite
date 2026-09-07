"""Password-reset flow: request a single-use token, redeem it with a new password.

Audited facade over AuthStore's reset-token storage. AuthStore stays
storage-only; the AuditLogStore is injected here — the same split as
auth.mfa.MfaService.

ENUMERATION-SAFETY (the crucial property). request_reset() does the same
work and returns the same shape whether or not the username exists. There is
no existence branch on the request path — AuthStore.create_reset_token()
always mints and stores a token, with no PBKDF2 or other expensive
per-branch work. `user_exists` is looked up once, purely to tag the audit
event; a not-found index lookup costs the same as a found one.

DELIVERY. request_reset() returns the raw token / link directly. In
production this method returns NOTHING and a background job emails the link
to the account's verified address. Returning it in an API response — which
is what this prototype does so tests and local dev can finish the flow — is
exactly the mistake you must never ship.
"""

import hashlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

_audit_log_dir = Path(__file__).resolve().parent.parent.parent / "audit-log"
if str(_audit_log_dir) not in sys.path:
    sys.path.insert(0, str(_audit_log_dir))

from audit_log import AuditEvent, AuditLogStore  # noqa: E402

from .models import ResetDelivery
from .store import DEFAULT_RESET_TTL_SECONDS, AuthStore

_AGENT = "platform/auth"
_RESET_LINK_BASE = "https://app.example/reset"  # placeholder; prototype only

_DELIVERY_NOTE = (
    "PROTOTYPE ONLY. In production request_reset() returns nothing; a "
    "background job emails this link to the account's verified address. It is "
    "returned here so local dev and tests can finish the flow — returning it "
    "in an API response is exactly the mistake you must never ship."
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _fingerprint(token: str) -> str:
    """A short, non-reversible tag for the audit log — never the raw token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


class PasswordResetService:
    def __init__(self, auth_store: AuthStore, *, audit_log: AuditLogStore):
        self.auth_store = auth_store
        self.audit_log = audit_log

    def _audit(self, action, actor, *, inputs, output=None, now=None) -> None:
        self.audit_log.append(AuditEvent(
            timestamp=(now or _utcnow()).isoformat(),
            agent=_AGENT, action=action, actor=actor,
            inputs=inputs, output=output,
        ))

    def request_reset(
        self,
        username: str,
        *,
        ttl_seconds: int = DEFAULT_RESET_TTL_SECONDS,
        now: Optional[datetime] = None,
    ) -> ResetDelivery:
        """Mint a reset token for `username` and (prototype) return it.

        Identical work and identical return shape for real and unknown
        usernames. Emits auth.password_reset.requested (with an internal
        `user_exists` field). Never logs the raw token.
        """
        stamp = now or _utcnow()
        token = self.auth_store.create_reset_token(
            username, ttl_seconds=ttl_seconds, now=stamp
        )
        expires_at = (stamp + timedelta(seconds=ttl_seconds)).isoformat()

        # Used ONLY to tag the audit event — never the response, never a branch.
        user_exists = self.auth_store.get_user(username) is not None
        self._audit(
            "auth.password_reset.requested", username,
            inputs={"username": username, "user_exists": user_exists},
            output={"token_fingerprint": _fingerprint(token), "expires_at": expires_at},
            now=stamp,
        )
        return ResetDelivery(
            username=username,
            reset_token=token,
            reset_link=f"{_RESET_LINK_BASE}?token={token}",
            requested_at=stamp.isoformat(),
            expires_at=expires_at,
            delivery_note=_DELIVERY_NOTE,
        )

    def redeem_reset(
        self,
        token: str,
        new_password: str,
        *,
        now: Optional[datetime] = None,
    ) -> bool:
        """Set a new password using a valid reset token.

        Returns True on success — the token is burned (single-use) and every
        one of that user's sessions is revoked. Returns False for any token
        problem (unknown / used / expired / user gone), all indistinguishable.
        Emits auth.password_reset.redeemed or .redeem_failed. Never logs the
        raw token or either password.
        """
        outcome = self.auth_store.consume_reset_token(token, new_password, now=now)
        fingerprint = _fingerprint(token)

        if outcome.status == "ok":
            self._audit(
                "auth.password_reset.redeemed", outcome.username,
                inputs={"username": outcome.username, "token_fingerprint": fingerprint},
                output={"sessions_invalidated": outcome.sessions_revoked},
                now=now,
            )
            return True

        self._audit(
            "auth.password_reset.redeem_failed", outcome.username or "unknown",
            inputs={"token_fingerprint": fingerprint, "reason": outcome.status},
            now=now,
        )
        return False
