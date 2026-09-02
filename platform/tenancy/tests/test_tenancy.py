import json
import sqlite3
from dataclasses import asdict

import pytest

from audit_log import AuditLogStore
from auth import AuthStore
from tenancy import (
    AlreadyAssigned,
    MissingTenantScope,
    NoMembership,
    ScopedTable,
    TenantNotFound,
    TenantScope,
    TenancyStore,
)

from fixtures import FICTIONAL_TENANTS, FICTIONAL_USERS, LEDGER_NOTES_SCHEMA


@pytest.fixture
def auth_store(tmp_path):
    s = AuthStore(tmp_path / "auth.db")
    for username, (password, role, _tenant) in FICTIONAL_USERS.items():
        s.create_user(username, password, role)
    yield s
    s.close()


@pytest.fixture
def store(tmp_path):
    s = TenancyStore(tmp_path / "tenancy.db")
    for tenant_id, display_name in FICTIONAL_TENANTS:
        s.create_tenant(tenant_id, display_name)
    yield s
    s.close()


@pytest.fixture
def assigned_store(store, auth_store):
    """A tenancy store with every fictional user assigned to their tenant."""
    for username, (_pw, _role, tenant_id) in FICTIONAL_USERS.items():
        store.assign_user(auth_store.get_user(username), tenant_id)
    return store


@pytest.fixture
def audit_log(tmp_path):
    log = AuditLogStore(tmp_path / "audit.db")
    yield log
    log.close()


@pytest.fixture(autouse=True)
def _audit_chain_stays_intact(audit_log):
    """Every test in this module — old and new — leaves the hash chain valid."""
    yield
    assert audit_log.verify_chain().ok is True


@pytest.fixture
def notes(tmp_path, audit_log):
    conn = sqlite3.connect(tmp_path / "demo.db")
    conn.executescript(LEDGER_NOTES_SCHEMA)
    yield ScopedTable(conn, "ledger_notes", audit_log)
    conn.close()


def _serialized_events(audit_log):
    return json.dumps([asdict(e) for e in audit_log.get_all()], default=str)


# ---------------------------------------------------------------------------
# 1. A user can only be associated with one tenant.
# ---------------------------------------------------------------------------

def test_user_belongs_to_exactly_one_tenant(store, auth_store):
    dana = auth_store.get_user("dana.acme")
    store.assign_user(dana, "acme-books")

    with pytest.raises(AlreadyAssigned):
        store.assign_user(dana, "globex-finance")

    # The original membership is untouched.
    assert store.membership_for("dana.acme").tenant_id == "acme-books"


# ---------------------------------------------------------------------------
# 2. THE IMPORTANT ONE: tenant A's data is never visible to tenant B.
# ---------------------------------------------------------------------------

def test_tenant_a_data_is_never_visible_to_tenant_b(assigned_store, notes):
    scope_a = assigned_store.scope_for("acme-books")
    scope_b = assigned_store.scope_for("globex-finance")

    # Each tenant writes a private note through its own scope.
    acme_note_id = notes.insert(
        scope_a, author="dana.acme", text="ACME confidential payroll figure"
    )
    notes.insert(
        scope_b, author="farah.globex", text="Globex confidential margin"
    )

    # Querying as tenant B returns ONLY tenant B's rows.
    rows_b = notes.all(scope_b)
    assert len(rows_b) == 1
    assert rows_b[0]["text"] == "Globex confidential margin"

    # Tenant A's confidential text appears in NOTHING returned to tenant B.
    texts_visible_to_b = [row["text"] for row in rows_b]
    assert "ACME confidential payroll figure" not in texts_visible_to_b

    # Even asking for tenant A's row by its exact id, as tenant B, returns nothing.
    assert notes.get(scope_b, acme_note_id) is None

    # And the mirror image: tenant A cannot see tenant B's row.
    rows_a = notes.all(scope_a)
    assert [row["text"] for row in rows_a] == ["ACME confidential payroll figure"]


# ---------------------------------------------------------------------------
# 3. A query with no tenant scope fails loudly, never returns everything.
# ---------------------------------------------------------------------------

def test_query_without_a_tenant_scope_fails_loudly(assigned_store, notes):
    scope_a = assigned_store.scope_for("acme-books")
    notes.insert(scope_a, author="dana.acme", text="row one")

    # None -> loud failure, not "return all rows".
    with pytest.raises(MissingTenantScope):
        notes.all(None)
    with pytest.raises(MissingTenantScope):
        notes.get(None, 1)
    with pytest.raises(MissingTenantScope):
        notes.insert(None, author="x", text="y")

    # A bare tenant_id string is not a scope either.
    with pytest.raises(MissingTenantScope):
        notes.all("acme-books")


# ---------------------------------------------------------------------------
# Supporting tests
# ---------------------------------------------------------------------------

def test_insert_cannot_smuggle_a_foreign_tenant_id(assigned_store, notes):
    scope_b = assigned_store.scope_for("globex-finance")

    with pytest.raises(ValueError):
        notes.insert(
            scope_b, tenant_id="acme-books", author="farah.globex", text="sneaky"
        )

    # Nothing was written under either tenant.
    assert notes.all(scope_b) == []
    assert notes.all(assigned_store.scope_for("acme-books")) == []


def test_scope_for_unknown_tenant_is_rejected(store):
    with pytest.raises(TenantNotFound):
        store.scope_for("no-such-org")


def test_assign_user_rejects_unknown_tenant(store, auth_store):
    with pytest.raises(TenantNotFound):
        store.assign_user(auth_store.get_user("dana.acme"), "no-such-org")


def test_assign_user_accepts_a_bare_username_string(store):
    membership = store.assign_user("evan.acme", "acme-books")
    assert membership.username == "evan.acme"
    assert store.membership_for("evan.acme").tenant_id == "acme-books"


def test_scope_for_user_returns_that_users_tenant(assigned_store):
    scope = assigned_store.scope_for_user("farah.globex")
    assert scope == TenantScope("globex-finance")


def test_scope_for_user_without_membership_fails(store):
    with pytest.raises(NoMembership):
        store.scope_for_user("unassigned.person")


def test_tenant_round_trips(store):
    tenant = store.get_tenant("initech-partners")
    assert tenant.display_name == "Initech Partners"
    assert tenant.created_at


# ---------------------------------------------------------------------------
# Activity logging
# ---------------------------------------------------------------------------

def test_scoped_insert_is_audited(assigned_store, notes, audit_log):
    scope = assigned_store.scope_for("acme-books")
    row_id = notes.insert(scope, actor="dana.acme", author="dana.acme", text="hello")

    events = audit_log.get_all()
    assert len(events) == 1
    event = events[0]
    assert event.action == "tenancy.scoped_insert"
    assert event.agent == "platform/tenancy"
    assert event.actor == "dana.acme"
    assert event.inputs == {
        "table": "ledger_notes",
        "tenant_id": "acme-books",
        "columns": ["author", "text"],  # names only, sorted
    }
    assert event.output == {"row_id": row_id}


def test_insert_does_not_log_the_row_values(assigned_store, notes, audit_log):
    scope = assigned_store.scope_for("globex-finance")
    notes.insert(
        scope, actor="farah.globex", author="farah.globex",
        text="Globex confidential margin target 42 percent",
    )
    assert "confidential margin target" not in _serialized_events(audit_log)


def test_scoped_reads_are_not_audited(assigned_store, notes, audit_log):
    scope = assigned_store.scope_for("acme-books")
    notes.insert(scope, actor="dana.acme", author="dana.acme", text="one")
    assert len(audit_log.get_all()) == 1

    notes.all(scope)
    notes.get(scope, 1)
    notes.all(scope)

    # Still just the single insert event — reads add nothing.
    assert len(audit_log.get_all()) == 1


def test_insert_without_an_actor_is_logged_as_unknown(assigned_store, notes, audit_log):
    scope = assigned_store.scope_for("acme-books")
    notes.insert(scope, author="dana.acme", text="no actor given")
    assert audit_log.get_all()[0].actor == "unknown"


def test_audit_chain_intact_after_cross_tenant_writes(assigned_store, notes, audit_log):
    scope_a = assigned_store.scope_for("acme-books")
    scope_b = assigned_store.scope_for("globex-finance")
    for i in range(3):
        notes.insert(scope_a, actor="dana.acme", author="dana.acme", text=f"a{i}")
        notes.insert(scope_b, actor="farah.globex", author="farah.globex", text=f"b{i}")

    assert len(audit_log.get_all()) == 6
    assert audit_log.verify_chain().ok is True
