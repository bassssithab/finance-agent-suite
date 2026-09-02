"""Data model for the prototype multi-tenancy layer. See docs/ARCHITECTURE.md."""

from dataclasses import dataclass

__all__ = ["Tenant", "Membership", "TenantScope"]


@dataclass
class Tenant:
    """One customer organization."""

    tenant_id: str      # unique slug, e.g. "acme-books"
    display_name: str   # e.g. "Acme Bookkeeping LLC"
    created_at: str     # ISO-8601 UTC


@dataclass
class Membership:
    """Associates an auth user with exactly one tenant.

    In the store, `username` is the primary key of the memberships table,
    so a user structurally cannot hold two memberships.
    """

    username: str
    tenant_id: str


@dataclass(frozen=True)
class TenantScope:
    """A capability object proving a caller is acting within one tenant.

    Frozen so it cannot be mutated after a store hands it out. The only way
    to obtain one is TenancyStore.scope_for() / scope_for_user(), both of
    which verify the tenant exists first. ScopedTable requires one on every
    read and write and builds the `WHERE tenant_id = ?` clause from it, so
    "forgetting" to filter by tenant is not an expressible operation.
    """

    tenant_id: str
