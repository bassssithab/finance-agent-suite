import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import pytest

from audit_log import AuditLogStore
from auth import AuthStore
from tenancy import ScopedTable, TenantScope, TenancyStore
from session import AuthenticatedSession, AuthFailure, SessionService

from fixtures import FICTIONAL_TENANTS, FICTIONAL_USERS, LEDGER_NOTES_SCHEMA


@pytest.fixture
def auth_store(tmp_path):
    s = AuthStore(tmp_path / "auth.db")
    for username, (password, role, _tenant) in FICTIONAL_USERS.items():
        s.create_user(username, password, role)
    yield s
    s.close()


@pytest.fixture
def tenancy_store(tmp_path, auth_store):
    s = TenancyStore(tmp_path / "tenancy.db")
    for tenant_id, display_name in FICTIONAL_TENANTS:
        s.create_tenant(tenant_id, display_name)
    for username, (_pw, _role, tenant_id) in FICTIONAL_USERS.items():
        if tenant_id is not None:
            s.assign_user(auth_store.get_user(username), tenant_id)
    yield s
    s.close()


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
def svc(auth_store, tenancy_store, audit_log):
    return SessionService(auth_store, tenancy_store, audit_log)


@pytest.fixture
def notes(tmp_path, audit_log):
    conn = sqlite3.connect(tmp_path / "demo.db")
    conn.executescript(LEDGER_NOTES_SCHEMA)
    yield ScopedTable(conn, "ledger_notes", audit_log)
    conn.close()


def _actions(audit_log):
    return [e.action for e in audit_log.get_all()]


def _serialized_events(audit_log):
    return json.dumps([asdict(e) for e in audit_log.get_all()], default=str)


def _password(username):
    return FICTIONAL_USERS[username][0]


def _session_row_count(tmp_path):
    raw = sqlite3.connect(tmp_path / "auth.db")
    try:
        return raw.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    finally:
        raw.close()


# ---------------------------------------------------------------------------
# 1. Successful authenticate returns a working TenantScope.
# ---------------------------------------------------------------------------

def test_authenticate_returns_a_working_tenant_scope(svc, notes):
    result = svc.authenticate("dana.acme", _password("dana.acme"))

    assert isinstance(result, AuthenticatedSession)
    assert result.user.username == "dana.acme"
    assert isinstance(result.scope, TenantScope)
    assert result.tenant_id == "acme-books"
    assert result.token

    # The scope is ready to use: it drives a tenant-scoped table straight away.
    row_id = notes.insert(result.scope, author="dana.acme", text="acme note")
    assert [r["text"] for r in notes.all(result.scope)] == ["acme note"]
    assert notes.get(result.scope, row_id)["tenant_id"] == "acme-books"


def test_authenticate_resolves_each_user_to_their_own_tenant(svc):
    dana = svc.authenticate("dana.acme", _password("dana.acme"))
    farah = svc.authenticate("farah.globex", _password("farah.globex"))

    assert dana.tenant_id == "acme-books"
    assert farah.tenant_id == "globex-finance"


# ---------------------------------------------------------------------------
# 2. Wrong password fails cleanly.
# ---------------------------------------------------------------------------

def test_wrong_password_fails_cleanly(svc, tmp_path):
    result = svc.authenticate("dana.acme", "not-the-password")

    assert result is AuthFailure.BAD_CREDENTIALS
    assert _session_row_count(tmp_path) == 0  # no token issued


def test_unknown_username_fails_the_same_way(svc):
    assert svc.authenticate("nobody.here", "whatever") is AuthFailure.BAD_CREDENTIALS


# ---------------------------------------------------------------------------
# 3. Valid login, but the user has no tenant yet — handled explicitly.
# ---------------------------------------------------------------------------

def test_valid_but_unassigned_user_is_handled_explicitly(svc, tmp_path):
    result = svc.authenticate("newbie.unassigned", _password("newbie.unassigned"))

    # A distinct state — not BAD_CREDENTIALS, not a session, not an exception.
    assert result is AuthFailure.NO_TENANT_ASSIGNED
    assert not isinstance(result, AuthenticatedSession)

    # The just-issued login token was rolled back: no usable session left behind.
    assert _session_row_count(tmp_path) == 0


# ---------------------------------------------------------------------------
# 4. validate() works for a live token.
# ---------------------------------------------------------------------------

def test_validate_works_for_a_live_token(svc, notes):
    logged_in = svc.authenticate("farah.globex", _password("farah.globex"))
    assert isinstance(logged_in, AuthenticatedSession)

    revalidated = svc.validate(logged_in.token)

    assert isinstance(revalidated, AuthenticatedSession)
    assert revalidated.user.username == "farah.globex"
    assert revalidated.tenant_id == "globex-finance"
    # Scope from a token-only re-derivation is just as usable.
    notes.insert(revalidated.scope, author="farah.globex", text="globex note")
    assert [r["text"] for r in notes.all(revalidated.scope)] == ["globex note"]


# ---------------------------------------------------------------------------
# 5. validate() fails cleanly for an expired or logged-out token.
# ---------------------------------------------------------------------------

def test_validate_fails_cleanly_for_expired_or_logged_out_token(svc, auth_store):
    issued = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)

    # -- expired --
    expiring = svc.authenticate(
        "dana.acme", _password("dana.acme"), ttl_seconds=60, now=issued
    )
    assert isinstance(expiring, AuthenticatedSession)
    assert svc.validate(expiring.token, now=issued + timedelta(hours=1)) is AuthFailure.INVALID_TOKEN

    # -- logged out --
    live = svc.authenticate("dana.acme", _password("dana.acme"))
    assert isinstance(live, AuthenticatedSession)
    auth_store.logout(live.token)
    assert svc.validate(live.token) is AuthFailure.INVALID_TOKEN

    # -- garbage --
    assert svc.validate("not-a-real-token") is AuthFailure.INVALID_TOKEN


# ---------------------------------------------------------------------------
# Supporting: a user assigned to an org only AFTER login.
# ---------------------------------------------------------------------------

def test_validate_reflects_a_tenant_assigned_after_login(svc, auth_store, tenancy_store):
    # newbie logs in before being assigned -> no session from authenticate().
    assert svc.authenticate(
        "newbie.unassigned", _password("newbie.unassigned")
    ) is AuthFailure.NO_TENANT_ASSIGNED

    # Now an admin assigns them, and they log in again.
    tenancy_store.assign_user(auth_store.get_user("newbie.unassigned"), "acme-books")
    result = svc.authenticate("newbie.unassigned", _password("newbie.unassigned"))

    assert isinstance(result, AuthenticatedSession)
    assert result.tenant_id == "acme-books"
    assert isinstance(svc.validate(result.token), AuthenticatedSession)


# ---------------------------------------------------------------------------
# Activity logging
# ---------------------------------------------------------------------------

def test_successful_login_is_audited(svc, audit_log):
    svc.authenticate("dana.acme", _password("dana.acme"))

    events = audit_log.get_all()
    assert len(events) == 1
    event = events[0]
    assert event.action == "session.login.succeeded"
    assert event.agent == "platform/session"
    assert event.actor == "dana.acme"
    assert event.output["tenant_id"] == "acme-books"
    assert event.output["role"] == "approver"
    assert len(event.output["token_fingerprint"]) == 12


def test_password_is_never_written_to_the_audit_log(svc, audit_log):
    secret = _password("dana.acme")
    svc.authenticate("dana.acme", secret)              # success path
    svc.authenticate("dana.acme", "not-the-password")  # failure path
    svc.authenticate("dana.acme", secret)              # success again

    assert secret not in _serialized_events(audit_log)


def test_bad_credentials_and_no_tenant_are_distinct_audit_actions(svc, audit_log):
    svc.authenticate("dana.acme", "wrong")
    svc.authenticate("newbie.unassigned", _password("newbie.unassigned"))
    svc.authenticate("nobody.here", "whatever")

    assert _actions(audit_log) == [
        "session.login.failed.bad_credentials",
        "session.login.failed.no_tenant",
        "session.login.failed.bad_credentials",
    ]


def test_no_tenant_login_audits_once_and_logs_no_logout(svc, audit_log, tmp_path):
    svc.authenticate("newbie.unassigned", _password("newbie.unassigned"))

    assert _actions(audit_log) == ["session.login.failed.no_tenant"]
    assert "session.logout" not in _actions(audit_log)
    assert _session_row_count(tmp_path) == 0  # token still rolled back


def test_validate_outcomes_are_audited(svc, audit_log):
    issued = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)
    session = svc.authenticate("dana.acme", _password("dana.acme"),
                               ttl_seconds=60, now=issued)

    svc.validate(session.token, now=issued + timedelta(seconds=10))   # live
    svc.validate(session.token, now=issued + timedelta(hours=1))      # expired
    svc.validate("not-a-real-token", now=issued)                      # garbage

    assert _actions(audit_log) == [
        "session.login.succeeded",
        "session.validate.succeeded",
        "session.validate.failed.invalid_token",
        "session.validate.failed.invalid_token",
    ]
    assert session.token not in _serialized_events(audit_log)


def test_logout_is_audited(svc, audit_log):
    session = svc.authenticate("farah.globex", _password("farah.globex"))
    svc.logout(session.token)

    assert _actions(audit_log) == ["session.login.succeeded", "session.logout"]
    logout_event = audit_log.get_all()[-1]
    assert logout_event.actor == "farah.globex"
    assert session.token not in _serialized_events(audit_log)


def test_audit_chain_intact_across_a_full_flow(svc, notes, audit_log):
    session = svc.authenticate("dana.acme", _password("dana.acme"))
    revalidated = svc.validate(session.token)
    notes.insert(revalidated.scope, actor=session.user.username,
                 author="dana.acme", text="flow note")
    svc.logout(session.token)

    assert _actions(audit_log) == [
        "session.login.succeeded",
        "session.validate.succeeded",
        "tenancy.scoped_insert",
        "session.logout",
    ]
    assert audit_log.verify_chain().ok is True
