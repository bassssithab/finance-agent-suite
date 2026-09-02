"""Fictional organizations and their people for the tenancy prototype.

Every tenant, person, and password here is invented. Not real companies,
not real people. Do not reuse anywhere.
"""

from auth import Role

# (tenant_id, display_name)
FICTIONAL_TENANTS = [
    ("acme-books", "Acme Bookkeeping LLC"),
    ("globex-finance", "Globex Finance Co"),
    ("initech-partners", "Initech Partners"),
]

# username -> (password, role, tenant_id)
FICTIONAL_USERS = {
    "dana.acme": ("acme-pw-placeholder-1", Role.APPROVER, "acme-books"),
    "evan.acme": ("acme-pw-placeholder-2", Role.PREPARER, "acme-books"),
    "farah.globex": ("globex-pw-placeholder-1", Role.APPROVER, "globex-finance"),
    "greta.initech": ("initech-pw-placeholder-1", Role.REVIEWER, "initech-partners"),
}

# The demo table used to show tenant-scoped data access.
LEDGER_NOTES_SCHEMA = """
CREATE TABLE ledger_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    author TEXT NOT NULL,
    text TEXT NOT NULL
);
"""
