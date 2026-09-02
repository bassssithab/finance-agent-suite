"""Fictional users and organizations for the combined-flow prototype.

Every person, org, and password here is invented. Not real. Do not reuse.

`newbie.unassigned` exists on purpose: a valid auth account with NO tenant
membership, to exercise the "login succeeds but user has no org yet" state.
"""

from auth import Role

# (tenant_id, display_name)
FICTIONAL_TENANTS = [
    ("acme-books", "Acme Bookkeeping LLC"),
    ("globex-finance", "Globex Finance Co"),
]

# username -> (password, role, tenant_id or None)
FICTIONAL_USERS = {
    "dana.acme": ("acme-pw-placeholder", Role.APPROVER, "acme-books"),
    "farah.globex": ("globex-pw-placeholder", Role.REVIEWER, "globex-finance"),
    "newbie.unassigned": ("newbie-pw-placeholder", Role.PREPARER, None),
}

# A demo table for showing the returned TenantScope is ready to use.
LEDGER_NOTES_SCHEMA = """
CREATE TABLE ledger_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    author TEXT NOT NULL,
    text TEXT NOT NULL
);
"""
