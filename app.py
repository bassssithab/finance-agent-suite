"""Streamlit web demo for the Finance Agent Suite.

A thin UI layer — not agent logic (so it lives at the repo root, not in any
agents/<name>/ folder). It only imports and calls existing agent/platform
functions, unmodified:

  * reconciliation-agent : run_reconciliation() against the sample_data CSVs
                           or an uploaded bank/ledger pair — the same run
                           demo.py narrates in the terminal.
  * vat-treatment-agent  : determine_vat_treatment() with a real Claude API
                           call. The API key comes from Streamlit secrets
                           (st.secrets["ANTHROPIC_API_KEY"]), never a .env.

Every run here uses throwaway in-memory audit-log / approval-queue stores,
exactly like agents/vat-treatment-agent/manual_live_run.py — nothing is
persisted.

Run:  streamlit run app.py
"""

import sys
import tempfile
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
PLATFORM = ROOT / "platform"

# Same sys.path approach as demo.py / manual_live_run.py: the agent and
# platform packages are imported in place, not installed.
for path in (
    ROOT / "agents" / "reconciliation-agent",
    ROOT / "agents" / "vat-treatment-agent",
    ROOT / "agents" / "vat-treatment-agent" / "evals",  # synthetic VAT corpus
    PLATFORM / "connectors",
    PLATFORM / "approvals",
    PLATFORM / "audit-log",
    PLATFORM / "knowledge",
):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from approvals import ApprovalQueue  # noqa: E402
from audit_log import AuditLogStore  # noqa: E402
from knowledge import KnowledgeBase  # noqa: E402
from reconciliation_agent import run_reconciliation  # noqa: E402
from vat_treatment_agent import InvoiceLineItem, determine_vat_treatment  # noqa: E402

from fixtures import ALL_DOCUMENTS  # noqa: E402  (agents/vat-treatment-agent/evals/fixtures.py)

SAMPLE_DATA = ROOT / "sample_data"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def money(amount) -> str:
    sign = "-" if amount < 0 else ""
    return f"{sign}${abs(amount):,.2f}"


def new_stores():
    """Fresh in-memory audit log + approval queue for one run (not persisted)."""
    audit_log = AuditLogStore(":memory:")
    approval_queue = ApprovalQueue(":memory:", audit_log)
    return audit_log, approval_queue


# --------------------------------------------------------------------------
# reconciliation view
# --------------------------------------------------------------------------
def render_reconciliation() -> None:
    st.header("Reconciliation Agent")
    st.write(
        "Matches bank-statement lines against ledger lines, flags exceptions, "
        "and submits a draft reconciliation report for human approval. All "
        "matching is deterministic code — no LLM involved."
    )

    source = st.radio(
        "Input data",
        ["Use the sample_data CSVs", "Upload my own"],
        horizontal=True,
    )

    bank_upload = ledger_upload = None
    if source == "Upload my own":
        col_a, col_b = st.columns(2)
        bank_upload = col_a.file_uploader("Bank statement CSV", type="csv")
        ledger_upload = col_b.file_uploader("Ledger (ERP export) CSV", type="csv")
        st.caption(
            "Bank columns: date, account, description, amount, balance, reference. "
            "Ledger columns: date, account, memo, debit, credit, reference."
        )

    if not st.button("Run reconciliation", type="primary"):
        return

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        if source == "Upload my own":
            if bank_upload is None or ledger_upload is None:
                st.error("Upload both a bank CSV and a ledger CSV first.")
                return
            bank_folder = tmp_path / "bank"
            ledger_folder = tmp_path / "ledger"
            bank_folder.mkdir()
            ledger_folder.mkdir()
            (bank_folder / "upload.csv").write_bytes(bank_upload.getvalue())
            (ledger_folder / "upload.csv").write_bytes(ledger_upload.getvalue())
        else:
            bank_folder = SAMPLE_DATA / "bank"
            ledger_folder = SAMPLE_DATA / "ledger"

        audit_log, approval_queue = new_stores()
        try:
            run = run_reconciliation(
                source_system="sample_co",
                bank_folder=bank_folder,
                ledger_folder=ledger_folder,
                audit_log=audit_log,
                approval_queue=approval_queue,
            )
            _render_reconciliation_result(run, audit_log)
        finally:
            audit_log.close()
            approval_queue.close()


def _render_reconciliation_result(run, audit_log) -> None:
    report = run.report
    exact = [p for p in report.matched if p.match_type == "exact"]
    tolerance = [p for p in report.matched if p.match_type == "tolerance"]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Exact matches", len(exact))
    c2.metric("Tolerance matches", len(tolerance))
    c3.metric("Exceptions", len(report.exceptions))
    c4.metric("Difference", money(report.difference))

    st.subheader("Matched pairs")
    if report.matched:
        st.dataframe(
            [
                {
                    "match type": p.match_type,
                    "bank ref": p.bank.reference or "—",
                    "bank amount": money(p.bank.amount),
                    "bank date": p.bank.date.isoformat(),
                    "ledger ref": p.ledger.reference or "—",
                    "ledger amount": money(p.ledger.amount),
                    "ledger date": p.ledger.date.isoformat(),
                    "date delta (days)": p.date_delta_days,
                }
                for p in report.matched
            ],
            hide_index=True,
        )
    else:
        st.write("None.")

    st.subheader("Exceptions")
    if report.exceptions:
        st.dataframe(
            [
                {
                    "side": e.side,
                    "amount": money(e.transaction.amount),
                    "date": e.transaction.date.isoformat(),
                    "reference": e.transaction.reference or "—",
                    "reason": e.reason,
                }
                for e in report.exceptions
            ],
            hide_index=True,
        )
    else:
        st.write("None — every transaction matched.")

    request = run.approval_request
    st.subheader("Approval")
    st.write(
        f"Request **#{request.id}** submitted by `{request.preparer}` — "
        f"status **{request.status}**, awaiting **{request.current_stage}**. "
        "Agents draft; humans approve."
    )

    st.subheader("Audit log")
    st.write("Every step is recorded in the append-only, hash-chained log:")
    for e in audit_log.get_all():
        st.write(f"- `{e.action}` (actor: {e.actor})")

    verification = audit_log.verify_chain()
    if verification.ok:
        st.success("verify_chain() → ok (hash chain intact, no tampering detected)")
    else:
        st.error(
            f"verify_chain() → broken at record {verification.broken_record_id}: "
            f"{verification.reason}"
        )


# --------------------------------------------------------------------------
# VAT treatment view
# --------------------------------------------------------------------------
def _get_api_key():
    try:
        key = st.secrets["ANTHROPIC_API_KEY"]
    except Exception:
        # No secrets.toml, no such key, or a malformed secrets file — all
        # mean "not configured" for our purposes.
        return None
    if isinstance(key, str):
        return key.strip() or None
    return key


def render_vat_treatment() -> None:
    st.header("VAT Treatment Agent")
    st.write(
        "Retrieves from `platform/knowledge` and drafts a cited VAT "
        "classification via the Claude API, then submits it for human "
        "approval. The draft is grounded strictly in the retrieved excerpts."
    )
    st.caption(
        "Knowledge corpus is a **synthetic test fixture** describing a "
        "fictional jurisdiction (\"Larenthia\") — not real VAT law. It exists "
        "to exercise retrieval, citation, and the exempt-vs-out-of-scope "
        "distinction."
    )

    api_key = _get_api_key()
    if not api_key:
        st.warning(
            "**ANTHROPIC_API_KEY is not configured.** The VAT agent makes a "
            "real Claude API call, so add the key to Streamlit secrets to run "
            "it:\n\n"
            "- locally: create `.streamlit/secrets.toml` with "
            "`ANTHROPIC_API_KEY = \"sk-ant-...\"`\n"
            "- deployed: set it in the app's **Settings → Secrets**\n\n"
            "The reconciliation agent needs no key and works without this."
        )

    # Defaults: the drop-shipment scenario from manual_live_run.py (the most
    # nuanced — exempt vs. out-of-scope).
    goods_type = st.text_input("goods_type", value="consumer electronics")
    customer_location = st.text_input(
        "customer_location", value="a country other than Larenthia"
    )
    transaction_type = st.text_input(
        "transaction_type",
        value="drop-shipped directly from a foreign supplier to the foreign customer",
    )

    if not st.button("Draft classification", type="primary", disabled=not api_key):
        return

    if not (goods_type and customer_location and transaction_type):
        st.error("Fill in all three fields.")
        return

    import anthropic

    kb = KnowledgeBase()
    kb.ingest(ALL_DOCUMENTS)
    audit_log, approval_queue = new_stores()
    client = anthropic.Anthropic(api_key=api_key)

    line_item = InvoiceLineItem(
        goods_type=goods_type,
        customer_location=customer_location,
        transaction_type=transaction_type,
    )

    try:
        with st.spinner("Retrieving knowledge and drafting via Claude…"):
            run = determine_vat_treatment(
                line_item=line_item,
                knowledge_base=kb,
                audit_log=audit_log,
                approval_queue=approval_queue,
                client=client,
            )
        _render_vat_result(run, audit_log)
    except anthropic.AnthropicError as exc:
        st.error(f"Claude API call failed: {exc}")
    finally:
        audit_log.close()
        approval_queue.close()


def _render_vat_result(run, audit_log) -> None:
    draft = run.draft

    if draft.refused:
        st.error(
            f"The model refused to answer (category: "
            f"`{draft.refusal_category}`). No approval request was submitted."
        )
        return

    st.subheader("Drafted classification")
    st.markdown(draft.answer_text)
    st.caption(f"Model: `{draft.model}` · draft for a human reviewer, not a final VAT position")

    st.subheader("Citations")
    for citation in draft.citations:
        st.write(f"- {citation}")

    st.subheader("Retrieved knowledge chunks")
    for event in audit_log.get_all():
        if event.action == "chunks_retrieved":
            for chunk in event.output["chunks"]:
                st.write(f"- `[{chunk['score']:.3f}]` {chunk['citation']}")

    request = run.approval_request
    st.subheader("Approval")
    st.write(
        f"Request **#{request.id}** submitted by `{request.preparer}` — "
        f"status **{request.status}**, awaiting **{request.current_stage}**."
    )

    verification = audit_log.verify_chain()
    if verification.ok:
        st.success("verify_chain() → ok (hash chain intact)")
    else:
        st.error(
            f"verify_chain() → broken at record {verification.broken_record_id}: "
            f"{verification.reason}"
        )


# --------------------------------------------------------------------------
# app shell
# --------------------------------------------------------------------------
def main() -> None:
    st.set_page_config(page_title="Finance Agent Suite — Demo", page_icon="📒")
    st.title("Finance Agent Suite")
    st.caption(
        "A web demo over the real agents. Every run uses throwaway in-memory "
        "audit-log and approval-queue stores — nothing is persisted."
    )

    agent = st.sidebar.radio(
        "Agent",
        ["Reconciliation Agent", "VAT Treatment Agent"],
    )

    if agent == "Reconciliation Agent":
        render_reconciliation()
    else:
        render_vat_treatment()


if __name__ == "__main__":
    main()
