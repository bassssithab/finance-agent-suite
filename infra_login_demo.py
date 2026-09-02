"""Standalone Streamlit prototype for the platform/session login flow.

This is an INFRASTRUCTURE prototype, deliberately separate from app.py and
the twelve agents. It is not linked from the main demo, not deployed, and
persists nothing to the repo. Its only job is to exercise the real
session.SessionService end to end in a browser:

    login form
      -> SessionService.authenticate(username, password)
         -> AuthenticatedSession  (token + auth.User + tenancy.TenantScope)
         -> AuthFailure.BAD_CREDENTIALS
         -> AuthFailure.NO_TENANT_ASSIGNED   (login ok, user has no org yet)
    later reruns
      -> SessionService.validate(token)       (re-derive the bundle)

The tenant-scoped notes demo shows the security property visually: a
logged-in user only ever sees their own tenant's rows, because every read
goes through tenancy.ScopedTable with a WHERE tenant_id = ? composed from
the session's TenantScope.

Every authenticate / validate / logout and every scoped write is recorded
in a real audit_log.AuditLogStore (hash-chained, tamper-evident) — shown in
the "Activity log" panel, with a live verify_chain() check. Passwords and
raw tokens are never logged.

Run:  streamlit run infra_login_demo.py

Fictional users/organizations only (reused from
platform/session/tests/fixtures.py). Nothing here is real.
"""

import importlib.util
import sqlite3
import sys
import tempfile
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
PLATFORM = ROOT / "platform"

# Same in-place import approach as app.py / demo.py — packages are not installed.
for _path in (
    PLATFORM / "session",
    PLATFORM / "auth",
    PLATFORM / "tenancy",
    PLATFORM / "audit-log",
):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from audit_log import AuditLogStore  # noqa: E402
from auth import AuthStore  # noqa: E402
from session import AuthenticatedSession, AuthFailure, SessionService  # noqa: E402
from tenancy import ScopedTable, TenancyStore  # noqa: E402


def _load_session_fixtures():
    """Load platform/session/tests/fixtures.py by path — one source of truth
    for the fictional users/tenants (the same trick app.py uses for agent
    fixtures)."""
    path = PLATFORM / "session" / "tests" / "fixtures.py"
    spec = importlib.util.spec_from_file_location("infra_login_demo_fixtures", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FIX = _load_session_fixtures()

# Pre-seeded notes so the tenant isolation is visible the moment you log in.
_SEED_NOTES = {
    "acme-books": [
        ("dana.acme", "Q3 close checklist drafted — waiting on bank confirmations."),
        ("dana.acme", "Reclassified the prepaid insurance amortization schedule."),
    ],
    "globex-finance": [
        ("farah.globex", "Intercompany netting run scheduled for month-end."),
        ("farah.globex", "Follow up with AP on the duplicate vendor records."),
    ],
}

_NOTES_TABLE = "ledger_notes"
_NOTES_SCHEMA = FIX.LEDGER_NOTES_SCHEMA.replace(
    "CREATE TABLE ", "CREATE TABLE IF NOT EXISTS "
)


# --------------------------------------------------------------------------
# seeded in-memory-ish infrastructure (temp files, rebuilt per rerun)
# --------------------------------------------------------------------------
@st.cache_resource
def _infra_dir() -> str:
    """A throwaway directory for this server process's SQLite files.

    Cached as a plain string (thread-safe). We deliberately do NOT cache the
    stores or their connections: sqlite3 connections are single-thread by
    default and Streamlit reruns can land on different threads. `:memory:`
    databases are per-connection and would be wiped every rerun, so we use
    temp files and rebuild lightweight store objects each run instead.
    """
    return tempfile.mkdtemp(prefix="infra_login_demo_")


class _Infra:
    def __init__(self) -> None:
        d = Path(_infra_dir())
        self.auth_store = AuthStore(d / "auth.db")
        self.tenancy_store = TenancyStore(d / "tenancy.db")
        # One hash-chained activity log, shared by the service and the table
        # (temp file, not :memory:, so it survives Streamlit reruns).
        self.audit_log = AuditLogStore(d / "audit.db")
        self.service = SessionService(
            self.auth_store, self.tenancy_store, self.audit_log
        )

        self._notes_path = d / "notes.db"
        notes_conn = sqlite3.connect(self._notes_path)
        notes_conn.executescript(_NOTES_SCHEMA)
        notes_conn.commit()
        self.notes = ScopedTable(notes_conn, _NOTES_TABLE, self.audit_log)

        self._seed_once()

    def _seed_once(self) -> None:
        if self.auth_store.get_user("dana.acme") is not None:
            return  # already seeded on an earlier rerun

        for tenant_id, display_name in FIX.FICTIONAL_TENANTS:
            self.tenancy_store.create_tenant(tenant_id, display_name)

        for username, (password, role, tenant_id) in FIX.FICTIONAL_USERS.items():
            self.auth_store.create_user(username, password, role)
            if tenant_id is not None:
                self.tenancy_store.assign_user(username, tenant_id)

        for tenant_id, rows in _SEED_NOTES.items():
            scope = self.tenancy_store.scope_for(tenant_id)
            for author, text in rows:
                self.notes.insert(scope, actor="seed", author=author, text=text)

    def tenant_note_counts(self) -> list[tuple[str, int]]:
        """Raw, scope-BYPASSING count per tenant — for the 'prove it' panel only."""
        raw = sqlite3.connect(self._notes_path)
        try:
            return raw.execute(
                f"SELECT tenant_id, COUNT(*) FROM {_NOTES_TABLE} "
                "GROUP BY tenant_id ORDER BY tenant_id"
            ).fetchall()
        finally:
            raw.close()


def get_infra() -> _Infra:
    return _Infra()


# --------------------------------------------------------------------------
# views
# --------------------------------------------------------------------------
def _render_test_logins() -> None:
    with st.expander("Fictional test logins"):
        st.markdown(
            "All invented — from `platform/session/tests/fixtures.py`.\n\n"
            "| username | password | tenant |\n"
            "|---|---|---|\n"
            "| `dana.acme` | `acme-pw-placeholder` | acme-books |\n"
            "| `farah.globex` | `globex-pw-placeholder` | globex-finance |\n"
            "| `newbie.unassigned` | `newbie-pw-placeholder` | _none — valid login, "
            "no organization yet_ |\n"
        )


def render_login(infra: _Infra) -> None:
    st.subheader("Log in")

    if st.session_state.pop("_session_expired", False):
        st.info("Your session expired or was ended — please log in again.")

    with st.form("login"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Log in")

    if submitted:
        result = infra.service.authenticate(username, password)

        if isinstance(result, AuthenticatedSession):
            st.session_state["session_token"] = result.token
            st.rerun()

        elif result is AuthFailure.BAD_CREDENTIALS:
            st.error("Incorrect username or password.")
            st.caption(
                "The service does not reveal which part was wrong — the auth "
                "layer is enumeration-safe, and SessionService preserves that."
            )

        elif result is AuthFailure.NO_TENANT_ASSIGNED:
            st.warning(
                "Your credentials are valid, but your account is not assigned "
                "to an organization yet. Ask an admin to add you to a tenant."
            )
            st.caption(
                "`authenticate()` returned `AuthFailure.NO_TENANT_ASSIGNED` and "
                "rolled back the session token it had briefly issued — there is "
                "nothing to persist, and no scope-less session was created."
            )

    _render_test_logins()


def render_logged_in(infra: _Infra, session: AuthenticatedSession) -> None:
    tenant = infra.tenancy_store.get_tenant(session.tenant_id)

    st.success(
        f"Logged in as **{session.user.username}** in tenant "
        f"**{session.tenant_id}** — {tenant.display_name}"
    )
    st.caption(f"role: `{session.user.role.value}`  ·  token persisted in st.session_state")

    if st.button("Log out"):
        infra.service.logout(session.token)  # emits a session.logout audit event
        del st.session_state["session_token"]
        st.rerun()

    st.divider()
    st.subheader(f"Your notes — tenant `{session.tenant_id}`")
    st.caption(
        "This list is `ScopedTable.all(scope)`. The `WHERE tenant_id = ?` is "
        "composed from your session's `TenantScope` — no code path on this page "
        "can return another tenant's rows."
    )

    rows = infra.notes.all(session.scope)
    if rows:
        st.table([{"author": r["author"], "text": r["text"]} for r in rows])
    else:
        st.write("_No notes yet._")

    with st.form("add_note"):
        text = st.text_input("New note")
        add = st.form_submit_button("Add note")
    if add and text.strip():
        infra.notes.insert(
            session.scope,
            actor=session.user.username,
            author=session.user.username,
            text=text.strip(),
        )
        st.rerun()

    with st.expander("Prove the isolation"):
        counts = dict(infra.tenant_note_counts())
        total = sum(counts.values())
        st.write(
            f"**{total}** notes exist across all tenants. "
            f"You can see **{len(rows)}** of them."
        )
        st.caption("Raw per-tenant counts (this query bypasses the scope — debug view only):")
        st.table(
            [{"tenant_id": tid, "notes": n} for tid, n in sorted(counts.items())]
        )
        st.write(
            "Log out and log back in as a user from a different tenant "
            "(`farah.globex`) — the scoped list above becomes a disjoint set."
        )

    with st.expander("Activity log"):
        st.caption(
            "Every authenticate / validate / logout and every scoped write is "
            "recorded in the same hash-chained, tamper-evident "
            "`audit_log.AuditLogStore` the agents use. Passwords and raw tokens "
            "are never in it — only a `sha256[:12]` token fingerprint."
        )
        events = infra.audit_log.get_all()
        st.table(
            [{"action": e.action, "actor": e.actor} for e in events[-15:]]
        )
        chain = infra.audit_log.verify_chain()
        if chain.ok:
            st.success(f"verify_chain() → ok ({len(events)} events, chain intact)")
        else:
            st.error(
                f"verify_chain() → broken at record {chain.broken_record_id}: "
                f"{chain.reason}"
            )


# --------------------------------------------------------------------------
# app shell
# --------------------------------------------------------------------------
def main() -> None:
    st.set_page_config(page_title="Infra: Login Demo", page_icon="🔐")
    st.title("Infrastructure prototype — login & tenant scoping")
    st.caption(
        "Standalone. Exercises the real `session.SessionService` "
        "(auth + tenancy). Not part of the agent demo, not deployed. "
        "Fictional users only; throwaway SQLite, nothing persisted."
    )

    infra = get_infra()
    token = st.session_state.get("session_token")

    if token is None:
        render_login(infra)
        return

    result = infra.service.validate(token)
    if isinstance(result, AuthenticatedSession):
        render_logged_in(infra, result)
    else:
        # INVALID_TOKEN (expired / logged out) or NO_TENANT_ASSIGNED
        # (membership removed mid-session) — drop it and return to login.
        del st.session_state["session_token"]
        st.session_state["_session_expired"] = True
        st.rerun()


if __name__ == "__main__":
    main()
