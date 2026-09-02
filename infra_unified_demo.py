"""LOCAL-ONLY Streamlit prototype: identity/tenancy layer -> real agents.

    ┌─────────────────────────────────────────────────────────────────┐
    │  LOCAL EXPLORATION ONLY.                                        │
    │  Never linked from app.py. Never deployed to Streamlit Cloud.   │
    │  Fictional users/tenants; throwaway SQLite; nothing persisted.  │
    └─────────────────────────────────────────────────────────────────┘

This file connects the prototype identity/access layer (platform/auth +
platform/tenancy + platform/session) to two of the real agents, purely so
the login-to-agent-run flow can be explored end to end on a laptop.

It reuses, unmodified:
  * infra_login_demo.py — the login screen and the seeded fictional
    users/tenants/AuditLogStore (via infra_login_demo.get_infra()).
  * app.py — the agent-calling functions (run_reconciliation,
    determine_vat_treatment) and their result renderers. No agent-calling
    code is duplicated here.

Two audit logs, kept honestly distinct:
  1. The agent runs against its OWN fresh in-memory AuditLogStore, exactly
     as app.py does. That store holds the agent's real, natively-written
     hash chain for the run (shown under "Agent result & its own audit
     trail").
  2. The shared identity AuditLogStore — the one the login/session events
     are already in — then gets:
       - one `agent.run` marker event (originally recorded here), tagged
         with the actor's username and tenant_id, and
       - a `agent.rerecorded/<action>` copy of each event from the agent's
         own log, appended afterward.
     The combined "Identity activity log" panel labels every row as either
     "recorded here" or "re-recorded (agent's own log)", so nothing looks
     more unified than it honestly is. verify_chain() still proves the
     combined sequence — as recorded here — is one unbroken chain.

(Aside: reconciliation-agent's run_reconciliation() does in fact accept an
injected audit_log=, so the agent's events could have been written straight
into the shared chain. Copy-and-relabel is the deliberate choice anyway:
the identity layer and the agents are separate prototypes and should not
share one chain, and the re-recording is visible rather than implied.)

Run:  streamlit run infra_unified_demo.py
"""

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
PLATFORM = ROOT / "platform"

for _path in (
    ROOT,
    PLATFORM / "session",
    PLATFORM / "auth",
    PLATFORM / "tenancy",
    PLATFORM / "audit-log",
):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import app as agent_app  # noqa: E402  (app.py at repo root — no st.* at import)
import infra_login_demo as login_demo  # noqa: E402
from audit_log import AuditEvent  # noqa: E402
from session import AuthenticatedSession  # noqa: E402

_RERECORD_PREFIX = "agent.rerecorded/"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------
# agents available in this demo
# --------------------------------------------------------------------------
def _available_agents() -> dict[str, str]:
    """reconciliation-agent always; vat-treatment-agent only with an API key.

    Every tenant sees the same list — per-tenant agent permissions are a
    future step, not built here.
    """
    agents = {"reconciliation-agent": "Reconciliation Agent — deterministic, no API key"}
    if agent_app._get_api_key():
        agents["vat-treatment-agent"] = "VAT Treatment Agent — real Claude API call"
    return agents


def _record_agent_run(infra, session, agent_name, params, run, agent_audit_log) -> None:
    """Write the run into the SHARED identity log: one marker + re-recorded copies."""
    approval_id = getattr(getattr(run, "approval_request", None), "id", None)

    infra.audit_log.append(AuditEvent(
        timestamp=_now(),
        agent="infra_unified_demo",
        action="agent.run",
        actor=session.user.username,
        inputs={"agent": agent_name, "tenant_id": session.tenant_id, "params": params},
        output={
            "approval_request_id": approval_id,
            "agent_internal_events": len(agent_audit_log.get_all()),
            "agent_internal_chain_ok": agent_audit_log.verify_chain().ok,
        },
    ))

    for e in agent_audit_log.get_all():
        infra.audit_log.append(AuditEvent(
            timestamp=_now(),  # when it was re-recorded, not the original time
            agent=agent_name,
            action=f"{_RERECORD_PREFIX}{e.action}",
            actor=session.user.username,
            inputs={
                "tenant_id": session.tenant_id,
                "rerecorded_from": "the agent's own audit log",
                "original_actor": e.actor,
                "original_action": e.action,
                "original_timestamp": e.timestamp,
            },
        ))


# --------------------------------------------------------------------------
# per-agent run panels (agent calls + renderers reused from app.py)
# --------------------------------------------------------------------------
def _run_reconciliation(infra, session) -> None:
    st.caption("Runs against the committed `sample_data/` bank + ledger CSVs.")
    if not st.button("Run reconciliation", type="primary"):
        return

    agent_audit_log, approval_queue = agent_app.new_stores()
    try:
        run = agent_app.run_reconciliation(
            source_system="sample_co",
            bank_folder=agent_app.SAMPLE_DATA / "bank",
            ledger_folder=agent_app.SAMPLE_DATA / "ledger",
            audit_log=agent_audit_log,
            approval_queue=approval_queue,
            preparer=session.user.username,
        )
        _record_agent_run(
            infra, session, "reconciliation-agent",
            {"source": "sample_data"}, run, agent_audit_log,
        )
        st.success("Run complete — re-recorded into the identity activity log below.")
        st.subheader("Agent result & its own audit trail")
        st.caption(
            "This is the agent's *own* separate hash-chained log for this run. "
            "The identity activity log at the bottom re-records these steps."
        )
        agent_app._render_reconciliation_result(run, agent_audit_log)
    finally:
        agent_audit_log.close()
        approval_queue.close()


def _run_vat(infra, session) -> None:
    import anthropic

    goods_type = st.text_input("goods_type", "consumer electronics")
    customer_location = st.text_input(
        "customer_location", "a country other than Larenthia"
    )
    transaction_type = st.text_input(
        "transaction_type",
        "drop-shipped directly from a foreign supplier to the foreign customer",
    )
    if not st.button("Draft classification", type="primary"):
        return
    if not (goods_type and customer_location and transaction_type):
        st.error("Fill in all three fields.")
        return

    agent_audit_log, approval_queue = agent_app.new_stores()
    kb = agent_app.KnowledgeBase()
    kb.ingest(agent_app.ALL_DOCUMENTS)
    try:
        with st.spinner("Retrieving knowledge and drafting via Claude…"):
            run = agent_app.determine_vat_treatment(
                line_item=agent_app.InvoiceLineItem(
                    goods_type=goods_type,
                    customer_location=customer_location,
                    transaction_type=transaction_type,
                ),
                knowledge_base=kb,
                audit_log=agent_audit_log,
                approval_queue=approval_queue,
                client=anthropic.Anthropic(api_key=agent_app._get_api_key()),
                preparer=session.user.username,
            )
        _record_agent_run(
            infra, session, "vat-treatment-agent",
            {
                "goods_type": goods_type,
                "customer_location": customer_location,
                "transaction_type": transaction_type,
            },
            run, agent_audit_log,
        )
        st.success("Run complete — re-recorded into the identity activity log below.")
        st.subheader("Agent result & its own audit trail")
        st.caption(
            "This is the agent's *own* separate hash-chained log for this run. "
            "The identity activity log at the bottom re-records these steps."
        )
        agent_app._render_vat_result(run, agent_audit_log)
    except anthropic.AnthropicError as exc:
        st.error(f"Claude API call failed: {exc}")
    finally:
        agent_audit_log.close()
        approval_queue.close()


_RUNNERS = {
    "reconciliation-agent": _run_reconciliation,
    "vat-treatment-agent": _run_vat,
}


# --------------------------------------------------------------------------
# combined activity log
# --------------------------------------------------------------------------
def _detail(event) -> str:
    src = event.inputs or {}
    bits = []
    if "agent" in src:
        bits.append(f"agent={src['agent']}")
    if "tenant_id" in src:
        bits.append(f"tenant={src['tenant_id']}")
    if "token_fingerprint" in src:
        bits.append(f"token={src['token_fingerprint']}")
    if isinstance(event.output, dict) and "reason" in event.output:
        bits.append(str(event.output["reason"]))
    return "  ·  ".join(bits)


def _render_combined_activity_log(infra) -> None:
    st.subheader("Identity activity log — the whole story, in order")
    events = infra.audit_log.get_all()

    show_validate = st.checkbox(
        "show every `session.validate.succeeded` (fires on each page rerun)",
        value=False,
    )

    rows, hidden = [], 0
    for i, e in enumerate(events, 1):
        if not show_validate and e.action == "session.validate.succeeded":
            hidden += 1
            continue
        rerecorded = e.action.startswith(_RERECORD_PREFIX)
        rows.append({
            "#": i,
            "origin": "re-recorded (agent's own log)" if rerecorded else "recorded here",
            "action": e.action[len(_RERECORD_PREFIX):] if rerecorded else e.action,
            "actor": e.actor,
            "detail": _detail(e),
        })
    st.dataframe(rows, hide_index=True)
    if hidden:
        st.caption(f"{hidden} `session.validate.succeeded` row(s) hidden — they fire on every page rerun.")

    st.caption(
        "**recorded here** — the identity layer wrote this event directly "
        "(logins, session checks, logout, and the `agent.run` marker).  "
        "**re-recorded** — a copy of an event the agent wrote to its *own* "
        "separate audit log during this run, appended here afterward and "
        "tagged with your username and tenant. The agent did not write into "
        "this chain directly; the identity layer and the agents are separate "
        "prototypes. `verify_chain()` still proves this combined sequence, "
        "*as recorded here*, has not been tampered with."
    )

    here = sum(1 for e in events if not e.action.startswith(_RERECORD_PREFIX))
    rerec = len(events) - here
    chain = infra.audit_log.verify_chain()
    if chain.ok:
        st.success(
            f"verify_chain() → ok — {len(events)} events "
            f"({here} recorded here + {rerec} re-recorded), one unbroken hash chain"
        )
    else:
        st.error(
            f"verify_chain() → broken at record {chain.broken_record_id}: {chain.reason}"
        )


# --------------------------------------------------------------------------
# dashboard
# --------------------------------------------------------------------------
def render_dashboard(infra, session: AuthenticatedSession) -> None:
    tenant = infra.tenancy_store.get_tenant(session.tenant_id)
    st.success(
        f"Logged in as **{session.user.username}** in tenant "
        f"**{session.tenant_id}** — {tenant.display_name}"
    )
    st.caption(f"role: `{session.user.role.value}`")

    if st.button("Log out"):
        infra.service.logout(session.token)
        del st.session_state["session_token"]
        st.rerun()

    st.divider()
    st.subheader("Agents")
    agents = _available_agents()
    st.caption(
        "Every tenant sees the same agent list. Per-tenant agent permissions "
        "are a future step — this demo just connects login → agent run → one "
        "combined audit trail."
    )
    choice = st.radio("Pick an agent", list(agents), format_func=lambda k: agents[k])
    if "vat-treatment-agent" not in agents:
        st.caption("_VAT Treatment Agent is hidden — no `ANTHROPIC_API_KEY` in `st.secrets`._")

    st.divider()
    _RUNNERS[choice](infra, session)

    st.divider()
    _render_combined_activity_log(infra)


# --------------------------------------------------------------------------
# app shell
# --------------------------------------------------------------------------
def _stop_if_deployed() -> None:
    """Best-effort guard: refuse to run on a hosted Streamlit environment."""
    if "/mount/src/" in str(ROOT) or os.environ.get("HOME") == "/home/adminuser":
        st.error(
            "`infra_unified_demo.py` is local-only and must not run on a "
            "deployed host. Stopping."
        )
        st.stop()


def main() -> None:
    st.set_page_config(page_title="Infra: Unified Demo (local only)", page_icon="🔐")
    st.title("Infra prototype — login → agent → one audit trail")
    st.warning(
        "**Local exploration only.** Connects the identity/tenancy prototype "
        "to the real agents. Never linked from `app.py`, never deployed. "
        "Fictional users; throwaway SQLite; nothing persisted."
    )
    _stop_if_deployed()

    infra = login_demo.get_infra()
    token = st.session_state.get("session_token")

    if token is None:
        login_demo.render_login(infra)  # the login screen, exactly as-is
        return

    result = infra.service.validate(token)
    if isinstance(result, AuthenticatedSession):
        render_dashboard(infra, result)
    else:
        del st.session_state["session_token"]
        st.session_state["_session_expired"] = True
        st.rerun()


if __name__ == "__main__":
    main()
