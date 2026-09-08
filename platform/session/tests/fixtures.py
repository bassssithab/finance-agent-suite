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
#
# Developer reference. This is the same login list infra_login_demo.py used to
# render in a "Fictional test logins" UI expander — that was removed because a
# table of usernames + passwords sitting on a login screen reads, at a glance,
# like a real credential leak, however clearly it is labelled. It lives only
# here now, for local dev:
#
#     dana.acme          Notreal0000   ->  acme-books
#     farah.globex       Notreal1111   ->  globex-finance
#     newbie.unassigned  Notreal2222   ->  (no tenant: "logged in, no org yet")
FICTIONAL_USERS = {
    "dana.acme": ("Notreal0000", Role.APPROVER, "acme-books"),
    "farah.globex": ("Notreal1111", Role.REVIEWER, "globex-finance"),
    "newbie.unassigned": ("Notreal2222", Role.PREPARER, None),
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
