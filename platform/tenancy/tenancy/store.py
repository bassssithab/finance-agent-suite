"""SQLite-backed tenant + membership store for the prototype tenancy layer.

Same shape as auth.AuthStore: construct with a db_path, the schema is
created on init, call close() when done. This store lives in its own DB file
(not auth's) — memberships reference auth users by username string, a loose
coupling that keeps the platform modules independent.

Design notes:

- `memberships.username` is the PRIMARY KEY, so a user cannot hold two
  memberships. `assign_user` surfaces that as AlreadyAssigned rather than
  silently moving the user.
- `scope_for()` verifies the tenant exists before handing out a TenantScope,
  so a typo'd tenant_id fails loudly instead of producing a scope that
  quietly matches no rows.
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union

from .models import Membership, Tenant, TenantScope

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tenants (
    tenant_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memberships (
    username TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id)
);
"""


class TenantExists(Exception):
    """Raised by create_tenant when the tenant_id is already registered."""


class TenantNotFound(Exception):
    """Raised when an operation names a tenant_id that does not exist."""


class AlreadyAssigned(Exception):
    """Raised by assign_user when the user already belongs to a tenant."""


class NoMembership(Exception):
    """Raised by scope_for_user when the user has no tenant membership."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _username_of(user_or_username) -> str:
    """Accept an auth.User (anything with a .username) or a bare string."""
    return getattr(user_or_username, "username", user_or_username)


class TenancyStore:
    def __init__(self, db_path: Union[str, Path]):
        self.db_path = str(db_path)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # -- tenants -------------------------------------------------------

    def create_tenant(
        self,
        tenant_id: str,
        display_name: str,
        *,
        now: Optional[datetime] = None,
    ) -> Tenant:
        created_at = (now or _utcnow()).isoformat()
        try:
            self._conn.execute(
                "INSERT INTO tenants (tenant_id, display_name, created_at) VALUES (?, ?, ?)",
                (tenant_id, display_name, created_at),
            )
        except sqlite3.IntegrityError:
            raise TenantExists(f"tenant_id {tenant_id!r} is already registered")
        self._conn.commit()
        return Tenant(tenant_id=tenant_id, display_name=display_name, created_at=created_at)

    def get_tenant(self, tenant_id: str) -> Optional[Tenant]:
        row = self._conn.execute(
            "SELECT tenant_id, display_name, created_at FROM tenants WHERE tenant_id = ?",
            (tenant_id,),
        ).fetchone()
        if row is None:
            return None
        return Tenant(tenant_id=row[0], display_name=row[1], created_at=row[2])

    # -- membership ---------------------------------------------------

    def assign_user(self, user_or_username, tenant_id: str) -> Membership:
        """Associate a user with exactly one tenant.

        `user_or_username` may be an auth.User or a bare username string.
        Raises TenantNotFound if the tenant does not exist, or
        AlreadyAssigned if the user already belongs to a tenant.
        """
        username = _username_of(user_or_username)
        if self.get_tenant(tenant_id) is None:
            raise TenantNotFound(f"no tenant with id {tenant_id!r}")

        existing = self.membership_for(username)
        if existing is not None:
            raise AlreadyAssigned(
                f"user {username!r} already belongs to tenant "
                f"{existing.tenant_id!r}; a user belongs to exactly one tenant"
            )

        self._conn.execute(
            "INSERT INTO memberships (username, tenant_id) VALUES (?, ?)",
            (username, tenant_id),
        )
        self._conn.commit()
        return Membership(username=username, tenant_id=tenant_id)

    def membership_for(self, user_or_username) -> Optional[Membership]:
        username = _username_of(user_or_username)
        row = self._conn.execute(
            "SELECT username, tenant_id FROM memberships WHERE username = ?",
            (username,),
        ).fetchone()
        if row is None:
            return None
        return Membership(username=row[0], tenant_id=row[1])

    # -- scopes -----------------------------------------------------

    def scope_for(self, tenant_id: str) -> TenantScope:
        """Return a TenantScope for an existing tenant.

        Raises TenantNotFound for an unknown tenant_id, so a typo cannot
        produce a scope that silently matches nothing.
        """
        if self.get_tenant(tenant_id) is None:
            raise TenantNotFound(f"no tenant with id {tenant_id!r}")
        return TenantScope(tenant_id=tenant_id)

    def scope_for_user(self, user_or_username) -> TenantScope:
        """Return the TenantScope for whichever tenant this user belongs to.

        Raises NoMembership if the user has not been assigned to a tenant.
        """
        username = _username_of(user_or_username)
        membership = self.membership_for(username)
        if membership is None:
            raise NoMembership(f"user {username!r} has no tenant membership")
        return TenantScope(tenant_id=membership.tenant_id)
