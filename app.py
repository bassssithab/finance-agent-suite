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
  * ap-agent             : process_invoice() — vision extraction of an
                           uploaded (or committed sample) invoice image, a
                           deterministic line-sum-vs-grand-total check, and
                           cited GL coding. Real Claude API calls; same key.
  * close-agent          : run_close_variance_analysis() — deterministic
                           budget-vs-actual variances by line item with
                           configurable threshold flagging, and cited
                           plain-English explanations for the flagged lines.
                           Real Claude API calls (only when something is
                           flagged); same key.
  * controls-sox-agent   : run_journal_entry_control_test() — deterministic
                           segregation-of-duties test over journal-entry
                           approvals, and cited deficiency narratives for the
                           flagged entries. Real Claude API calls (only when
                           there is a violation); same key.

Every run here uses throwaway in-memory audit-log / approval-queue stores,
exactly like agents/vat-treatment-agent/manual_live_run.py — nothing is
persisted.

Run:  streamlit run app.py
"""

import importlib.util
import sys
import tempfile
from decimal import Decimal
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
    ROOT / "agents" / "ap-agent",
    ROOT / "agents" / "close-agent",
    ROOT / "agents" / "controls-sox-agent",
    PLATFORM / "connectors",
    PLATFORM / "approvals",
    PLATFORM / "audit-log",
    PLATFORM / "knowledge",
):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from ap_agent import process_invoice  # noqa: E402
from approvals import ApprovalQueue  # noqa: E402
from audit_log import AuditLogStore  # noqa: E402
from close_agent import FlagThresholds, run_close_variance_analysis  # noqa: E402
from connectors import ConnectorParseError, FileDocumentConnector  # noqa: E402
from controls_sox_agent import ControlPolicy, run_journal_entry_control_test  # noqa: E402
from knowledge import KnowledgeBase  # noqa: E402
from reconciliation_agent import run_reconciliation  # noqa: E402
from vat_treatment_agent import InvoiceLineItem, determine_vat_treatment  # noqa: E402

from fixtures import ALL_DOCUMENTS  # noqa: E402  (agents/vat-treatment-agent/evals/fixtures.py)


def _load_fixture_module(module_name: str, agent_dir: str):
    """Load an agent's evals/fixtures.py by file path under a unique name.

    Every agent's evals/fixtures.py defines `ALL_DOCUMENTS`, so they collide
    with each other and with the VAT `fixtures` module already imported above.
    Loading each by path under its own module name keeps them separate — the
    same trick the AP view has always used.
    """
    path = ROOT / "agents" / agent_dir / "evals" / "fixtures.py"
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


AP_CHART_OF_ACCOUNTS_DOCS = _load_fixture_module(
    "ap_agent_evals_fixtures", "ap-agent"
).ALL_DOCUMENTS
CLOSE_ACCOUNTING_POLICY_DOCS = _load_fixture_module(
    "close_agent_evals_fixtures", "close-agent"
).ALL_DOCUMENTS
CONTROLS_POLICY_DOCS = _load_fixture_module(
    "controls_sox_agent_evals_fixtures", "controls-sox-agent"
).ALL_DOCUMENTS

SAMPLE_DATA = ROOT / "sample_data"
AP_INVOICE_DIR = ROOT / "agents" / "ap-agent" / "evals" / "fixtures" / "invoices"
AP_SAMPLE_INVOICES = {
    "Clean — office supplies (totals tie, GL coding suggested)": "clean_office_supplies.png",
    "Consulting services (advisory fees, reimbursable travel)": "consulting_services.png",
    "Mismatched totals (deterministic check flags a bad grand total)": "mismatched_totals.png",
}

CLOSE_FIXTURES_DIR = ROOT / "agents" / "close-agent" / "evals" / "fixtures"
CLOSE_SAMPLE_PERIODS = {
    "2026-07 — eventful (large % and $ variances, unbudgeted spend)": "2026-07",
    "2026-08 — quiet (every line within ±5% — nothing flagged, no Claude call)": "2026-08",
    "2026-09 — edge cases (revenue miss, budgeted line with no actual, unbudgeted fees)": "2026-09",
}

CONTROLS_JE_DIR = (
    ROOT / "agents" / "controls-sox-agent" / "evals" / "fixtures" / "journal_entries"
)
CONTROLS_SAMPLE_BATCHES = {
    "Clean batch — 5 compliant entries (no violations, no Claude call)": "clean_batch.csv",
    "SoD violations — self-approval, missing 2nd approver, duplicate approvers, "
    "preparer as 2nd approver": "sod_violations.csv",
    "Edge cases — entry exactly at threshold, unapproved entry, case/whitespace "
    "name match": "edge_cases.csv",
}


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


def _render_audit_log(audit_log) -> None:
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


def _render_approval(request) -> None:
    st.subheader("Approval")
    st.write(
        f"Request **#{request.id}** submitted by `{request.preparer}` — "
        f"status **{request.status}**, awaiting **{request.current_stage}**. "
        "Agents draft; humans approve."
    )


def _missing_key_warning(what: str) -> None:
    st.warning(
        f"**ANTHROPIC_API_KEY is not configured.** {what} so add the key to "
        "Streamlit secrets to run it:\n\n"
        "- locally: create `.streamlit/secrets.toml` with "
        "`ANTHROPIC_API_KEY = \"sk-ant-...\"`\n"
        "- deployed: set it in the app's **Settings → Secrets**\n\n"
        "The reconciliation agent needs no key and works without this."
    )


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
# AP invoice view
# --------------------------------------------------------------------------
def render_ap_invoice() -> None:
    st.header("AP Invoice Agent")
    st.write(
        "Extracts an invoice image via Claude vision, runs a **deterministic** "
        "line-sum-vs-grand-total check in plain code, retrieves from "
        "`platform/knowledge` to draft cited GL coding, then submits the draft "
        "for human approval. The model only transcribes and classifies — it "
        "never does the arithmetic."
    )
    st.caption(
        "The chart of accounts is a **synthetic test fixture** (\"Larenthia "
        "Trading Co\", the same fictional jurisdiction as the VAT agent) — not "
        "a real GL structure. It exists to exercise retrieval, citation, and "
        "the \"account not in the chart → don't guess\" path."
    )

    api_key = _get_api_key()
    if not api_key:
        st.warning(
            "**ANTHROPIC_API_KEY is not configured.** The AP agent makes real "
            "Claude API calls (vision extraction + GL coding), so add the key "
            "to Streamlit secrets to run it:\n\n"
            "- locally: create `.streamlit/secrets.toml` with "
            "`ANTHROPIC_API_KEY = \"sk-ant-...\"`\n"
            "- deployed: set it in the app's **Settings → Secrets**\n\n"
            "The reconciliation agent needs no key and works without this."
        )

    source = st.radio(
        "Invoice",
        ["Use a committed sample invoice", "Upload an invoice image"],
        horizontal=True,
    )

    document_bytes = None
    document_id = None
    if source == "Upload an invoice image":
        upload = st.file_uploader("Invoice image", type=["png", "jpg", "jpeg"])
        if upload is not None:
            document_bytes = upload.getvalue()
            document_id = "upload" + Path(upload.name).suffix.lower()
    else:
        label = st.radio("Sample invoice", list(AP_SAMPLE_INVOICES), index=0)
        document_id = AP_SAMPLE_INVOICES[label]
        st.caption(
            "Sample invoices are fictional test fixtures generated by the eval "
            "suite (`agents/ap-agent/evals/generate_sample_invoices.py`) — not "
            "real vendor documents."
        )

    if not st.button("Process invoice", type="primary", disabled=not api_key):
        return

    if source == "Upload an invoice image" and document_bytes is None:
        st.error("Upload a PNG or JPEG invoice image first.")
        return

    import anthropic

    kb = KnowledgeBase()
    kb.ingest(AP_CHART_OF_ACCOUNTS_DOCS)
    audit_log, approval_queue = new_stores()
    client = anthropic.Anthropic(api_key=api_key)

    with tempfile.TemporaryDirectory() as tmp:
        if source == "Upload an invoice image":
            folder = Path(tmp)
            (folder / document_id).write_bytes(document_bytes)
        else:
            folder = AP_INVOICE_DIR

        connector = FileDocumentConnector(source_system="demo_co", folder=folder)

        try:
            with st.spinner("Extracting the invoice and drafting GL coding via Claude…"):
                run = process_invoice(
                    document_id=document_id,
                    document_connector=connector,
                    knowledge_base=kb,
                    audit_log=audit_log,
                    approval_queue=approval_queue,
                    client=client,
                )
            _render_ap_result(run, audit_log)
        except anthropic.AnthropicError as exc:
            st.error(f"Claude API call failed: {exc}")
        finally:
            audit_log.close()
            approval_queue.close()


def _render_ap_result(run, audit_log) -> None:
    if run.draft is None:
        extraction = run.extraction
        if extraction.refused:
            st.error(
                f"The model refused to extract the image (category: "
                f"`{extraction.refusal_category}`). No approval request was "
                "submitted."
            )
        else:
            st.error(
                f"Extraction failed: {extraction.parse_error}. No approval "
                "request was submitted."
            )
        _render_ap_audit(audit_log)
        return

    draft = run.draft
    inv = draft.invoice

    st.subheader("Extracted invoice")
    c1, c2, c3 = st.columns(3)
    c1.metric("Vendor", inv.vendor_name or "—")
    c2.metric("Invoice #", inv.invoice_number or "—")
    c3.metric("Date", inv.invoice_date or "—")
    c4, c5 = st.columns(2)
    c4.metric("Currency", inv.currency or "—")
    c5.metric("Extraction confidence", f"{inv.extraction_confidence:.2f}")
    st.caption(
        f"Model: `{draft.model}` · self-reported confidence · draft for a human "
        "reviewer, not a posted invoice"
    )

    st.subheader("Line items")
    st.dataframe(
        [
            {
                "#": i,
                "description": li.description,
                "quantity": str(li.quantity),
                "unit price": str(li.unit_price),
                "line total": f"{inv.currency} {li.line_total}".strip(),
            }
            for i, li in enumerate(inv.line_items)
        ],
        hide_index=True,
    )
    st.metric("Grand total (as printed)", f"{inv.currency} {inv.grand_total}".strip())

    st.subheader("Deterministic sanity check")
    sc = draft.sanity_check
    if draft.discrepancy_flagged:
        st.error(
            "**DISCREPANCY FLAGGED.** Line-item sum "
            f"**{sc.computed_line_sum}** vs. stated grand total "
            f"**{sc.stated_grand_total}** — difference **{sc.difference}**. "
            "The draft is still submitted for approval (the agent drafts; it "
            "doesn't get to block), carrying this flag."
        )
        if sc.line_total_issues:
            st.write("Lines where quantity × unit price ≠ the stated line total:")
            st.dataframe(
                [
                    {
                        "#": issue.line_index,
                        "description": issue.description,
                        "quantity": str(issue.quantity),
                        "unit price": str(issue.unit_price),
                        "computed": str(issue.computed_line_total),
                        "invoice says": str(issue.stated_line_total),
                        "difference": str(issue.difference),
                    }
                    for issue in sc.line_total_issues
                ],
                hide_index=True,
            )
    else:
        st.success(
            f"Totals tie — line-item sum ({sc.computed_line_sum}) equals the "
            f"stated grand total ({sc.stated_grand_total}), every line's "
            "arithmetic checks out."
        )

    st.subheader("GL coding suggestions")
    if draft.coding_skipped_reason:
        st.info(f"GL coding skipped: `{draft.coding_skipped_reason}`.")
    else:
        st.dataframe(
            [
                {
                    "line #": s.line_index,
                    "description": s.description,
                    "account": (
                        f"{s.account_code} {s.account_name}"
                        if s.account_code
                        else "— not in chart"
                    ),
                    "rationale": s.rationale,
                    "citation": s.citation or "—",
                }
                for s in draft.gl_suggestions
            ],
            hide_index=True,
        )
        if draft.coding_citations:
            st.write("Knowledge chunks the draft is grounded in:")
            for citation in draft.coding_citations:
                st.write(f"- {citation}")

    request = run.approval_request
    st.subheader("Approval")
    st.write(
        f"Request **#{request.id}** submitted by `{request.preparer}` — "
        f"status **{request.status}**, awaiting **{request.current_stage}**. "
        "Agents draft; humans approve."
    )

    _render_ap_audit(audit_log)


def _render_ap_audit(audit_log) -> None:
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
# Close agent — variance analysis view
# --------------------------------------------------------------------------
def render_close_variance() -> None:
    st.header("Close Agent — Variance Analysis")
    st.write(
        "Computes budget-vs-actual variances for every line item in "
        "**deterministic** code, flags the lines that breach configurable "
        "percentage / dollar thresholds, retrieves from `platform/knowledge` "
        "to draft a cited plain-English explanation for each flagged line, "
        "then submits the whole report for human approval. The model only "
        "writes prose — it never touches the arithmetic."
    )
    st.caption(
        "The accounting-policy corpus is a **synthetic test fixture** "
        "(\"Larenthia Trading Co\", the same fictional entity as the other "
        "agents) — not a real close policy. It exists to exercise retrieval, "
        "citation, and the grounded-vs-ungrounded explanation paths."
    )

    api_key = _get_api_key()
    if not api_key:
        _missing_key_warning(
            "The close agent makes a real Claude API call to explain flagged "
            "variances (a period with nothing flagged makes no call),"
        )

    col_pct, col_amt = st.columns(2)
    pct = col_pct.number_input(
        "Percentage threshold (%) — flag |actual − budget| / budget ≥ this",
        min_value=0.0, value=10.0, step=1.0,
    )
    amt = col_amt.number_input(
        "Absolute $ threshold — flag |actual − budget| ≥ this (0 = disabled)",
        min_value=0, value=25000, step=1000,
    )
    thresholds = FlagThresholds(
        pct=Decimal(str(pct / 100)) if pct > 0 else None,
        amount=Decimal(str(amt)) if amt > 0 else None,
    )

    source = st.radio(
        "Input data",
        ["Use a committed sample period", "Upload my own budget + actuals CSVs"],
        horizontal=True,
    )

    budget_upload = actuals_upload = None
    upload_period = ""
    if source == "Upload my own budget + actuals CSVs":
        col_a, col_b = st.columns(2)
        budget_upload = col_a.file_uploader("Budget CSV", type="csv")
        actuals_upload = col_b.file_uploader("Actuals CSV", type="csv")
        upload_period = st.text_input(
            "Period to analyze (the exact value in the CSVs' `period` column, e.g. 2026-07)"
        )
        st.caption(
            "Both CSVs share the schema "
            "`period,account,line_item,category,amount,currency` "
            "(`category` and `currency` optional)."
        )
    else:
        label = st.radio("Sample period", list(CLOSE_SAMPLE_PERIODS), index=0)
        sample_period = CLOSE_SAMPLE_PERIODS[label]
        st.caption(
            "Sample budget/actuals are fictional figures from the close-agent "
            "eval suite (`agents/close-agent/evals/fixtures/`) — not real ledgers."
        )

    if not st.button("Run variance analysis", type="primary", disabled=not api_key):
        return

    import anthropic

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        if source == "Upload my own budget + actuals CSVs":
            if budget_upload is None or actuals_upload is None:
                st.error("Upload both a budget CSV and an actuals CSV first.")
                return
            if not upload_period.strip():
                st.error("Enter the period to analyze.")
                return
            period = upload_period.strip()
            budget_folder = tmp_path / "budget"
            actuals_folder = tmp_path / "actuals"
            budget_folder.mkdir()
            actuals_folder.mkdir()
            (budget_folder / "upload.csv").write_bytes(budget_upload.getvalue())
            (actuals_folder / "upload.csv").write_bytes(actuals_upload.getvalue())
        else:
            period = sample_period
            budget_folder = CLOSE_FIXTURES_DIR / "budget"
            actuals_folder = CLOSE_FIXTURES_DIR / "actuals"

        kb = KnowledgeBase()
        kb.ingest(CLOSE_ACCOUNTING_POLICY_DOCS)
        audit_log, approval_queue = new_stores()
        client = anthropic.Anthropic(api_key=api_key)

        try:
            with st.spinner("Computing variances and drafting explanations via Claude…"):
                run = run_close_variance_analysis(
                    source_system="demo_co",
                    period=period,
                    budget_folder=budget_folder,
                    actuals_folder=actuals_folder,
                    knowledge_base=kb,
                    audit_log=audit_log,
                    approval_queue=approval_queue,
                    client=client,
                    thresholds=thresholds,
                )
            _render_close_result(run, audit_log)
        except anthropic.AnthropicError as exc:
            st.error(f"Claude API call failed: {exc}")
        except ValueError as exc:
            st.error(f"Could not run the analysis: {exc}")
        finally:
            audit_log.close()
            approval_queue.close()


def _render_close_result(run, audit_log) -> None:
    report = run.report
    summary = report.summary()

    c1, c2, c3 = st.columns(3)
    c1.metric("Line items", summary["line_count"])
    c2.metric("Flagged", summary["flagged_count"])
    c3.metric("Currency", report.currency)
    c4, c5, c6 = st.columns(3)
    c4.metric("Total budget", money(Decimal(summary["total_budget"])))
    c5.metric("Total actual", money(Decimal(summary["total_actual"])))
    c6.metric("Total variance", money(Decimal(summary["total_variance"])))
    st.caption(
        f"Period {report.period} · model `{report.model or '—'}` · draft for a "
        "reviewer, not a final close position"
    )

    st.subheader("Variance table")
    st.dataframe(
        [
            {
                "": "🚩" if lv.flagged else "",
                "account": lv.account,
                "line item": lv.line_item,
                "category": lv.category or "—",
                "budget": money(lv.budget_amount),
                "actual": money(lv.actual_amount),
                "variance": money(lv.variance),
                "pct": "—" if lv.pct_variance is None else f"{lv.pct_variance * 100:.1f}%",
                "direction": lv.direction.replace("_", " "),
                "presence": lv.presence.replace("_", " "),
            }
            for lv in report.line_variances
        ],
        hide_index=True,
    )

    st.subheader("Flagged variances")
    if report.flagged:
        for lv in report.flagged:
            st.markdown(
                f"**{lv.account} · {lv.line_item}** — {lv.direction.replace('_', ' ')}"
            )
            for reason in lv.flag_reasons:
                st.write(f"- {reason}")
    else:
        st.success("Nothing breached the thresholds — no explanations needed.")

    st.subheader("Drafted explanations")
    if report.explanations_skipped_reason:
        st.info(f"Explanations skipped: `{report.explanations_skipped_reason}`.")
    else:
        for e in report.explanations:
            st.markdown(f"**{e.account} · {e.line_item}**")
            st.write(e.explanation)
            if e.primary_drivers:
                st.caption(f"Primary drivers: {', '.join(e.primary_drivers)}")
            if e.citations:
                for citation in e.citations:
                    st.write(f"- {citation}")
            else:
                st.caption("Ungrounded — no policy excerpt cited.")

    _render_approval(run.approval_request)
    _render_audit_log(audit_log)


# --------------------------------------------------------------------------
# Controls (SoD) agent view
# --------------------------------------------------------------------------
def render_controls_sod() -> None:
    st.header("Controls (SoD) Agent")
    st.write(
        "Tests every journal entry against a **segregation-of-duties** control "
        "in deterministic code — the preparer may not also approve, the two "
        "named approvers must differ, and an entry at or above a configurable "
        "dollar threshold needs a second approver — then retrieves from "
        "`platform/knowledge` to draft a cited plain-English deficiency "
        "narrative for each exception and submits the control-test report for "
        "human approval. `autonomy: draft_only`."
    )
    st.caption(
        "The internal-controls-policy corpus is a **synthetic test fixture** "
        "(\"Larenthia Trading Co\") — not a real controls policy. It exists to "
        "exercise retrieval, citation, and the grounded-vs-ungrounded paths."
    )

    api_key = _get_api_key()
    if not api_key:
        _missing_key_warning(
            "The controls agent makes a real Claude API call to narrate flagged "
            "exceptions (a batch with no violations makes no call),"
        )

    threshold = st.number_input(
        "Dual-approval threshold ($) — an entry at or above this needs two distinct approvers",
        min_value=0, value=50000, step=1000,
    )
    policy = ControlPolicy(dual_approval_threshold=Decimal(str(threshold)))

    source = st.radio(
        "Journal entries",
        ["Use a committed sample batch", "Upload my own journal-entry CSV"],
        horizontal=True,
    )

    je_upload = None
    if source == "Upload my own journal-entry CSV":
        je_upload = st.file_uploader("Journal-entry CSV", type="csv")
        st.caption(
            "Schema: `entry_id,date,account,amount,preparer,approver_1,approver_2` "
            "(`approver_2` and a trailing `currency` column optional)."
        )
    else:
        label = st.radio("Sample batch", list(CONTROLS_SAMPLE_BATCHES), index=1)
        sample_file = CONTROLS_SAMPLE_BATCHES[label]
        st.caption(
            "Sample batches are fictional entries from the controls-sox-agent "
            "eval suite (`agents/controls-sox-agent/evals/fixtures/"
            "journal_entries/`) — not real journal entries."
        )

    if not st.button("Run control test", type="primary", disabled=not api_key):
        return

    import anthropic

    with tempfile.TemporaryDirectory() as tmp:
        entries_folder = Path(tmp) / "journal_entries"
        entries_folder.mkdir()

        if source == "Upload my own journal-entry CSV":
            if je_upload is None:
                st.error("Upload a journal-entry CSV first.")
                return
            (entries_folder / "upload.csv").write_bytes(je_upload.getvalue())
        else:
            (entries_folder / sample_file).write_text(
                (CONTROLS_JE_DIR / sample_file).read_text()
            )

        kb = KnowledgeBase()
        kb.ingest(CONTROLS_POLICY_DOCS)
        audit_log, approval_queue = new_stores()
        client = anthropic.Anthropic(api_key=api_key)

        try:
            with st.spinner("Testing journal-entry approvals and drafting narratives via Claude…"):
                run = run_journal_entry_control_test(
                    source_system="demo_co",
                    entries_folder=entries_folder,
                    knowledge_base=kb,
                    audit_log=audit_log,
                    approval_queue=approval_queue,
                    client=client,
                    policy=policy,
                )
            _render_controls_result(run, audit_log)
        except anthropic.AnthropicError as exc:
            st.error(f"Claude API call failed: {exc}")
        except (ValueError, ConnectorParseError) as exc:
            st.error(f"Could not run the control test: {exc}")
        finally:
            audit_log.close()
            approval_queue.close()


def _render_controls_result(run, audit_log) -> None:
    report = run.report
    summary = report.summary()

    st.caption(
        f"Control **{report.control_id}** — {report.control_name} · "
        f"model `{report.model or '—'}` · draft for a reviewer, not a final "
        "controls conclusion"
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Entries tested", summary["entries_tested"])
    c2.metric("With violations", summary["entries_with_violations"])
    c3.metric("Violations", summary["violation_count"])
    c4.metric("Dual-approval required", summary["dual_approval_required_count"])
    if summary["violations_by_code"]:
        st.write(
            "By type: "
            + ", ".join(
                f"`{code}` × {count}"
                for code, count in sorted(summary["violations_by_code"].items())
            )
        )

    st.subheader("Entries tested")
    st.dataframe(
        [
            {
                "": "🚩" if not r.passed else "✅",
                "entry": r.entry_id,
                "date": r.date,
                "account": r.account,
                "amount": money(r.amount),
                "preparer": r.preparer,
                "approver 1": r.approver_1 or "—",
                "approver 2": r.approver_2 or "—",
                "dual approval req.": "yes" if r.dual_approval_required else "no",
            }
            for r in report.results
        ],
        hide_index=True,
    )

    st.subheader("Violations")
    violation_rows = [
        {
            "entry": r.entry_id,
            "code": v.code,
            "reason (deterministic)": v.detail,
            "amount": money(r.amount),
            "preparer": r.preparer,
            "approvers": ", ".join(
                a for a in (r.approver_1, r.approver_2) if a
            ) or "—",
        }
        for r in report.results
        for v in r.violations
    ]
    if violation_rows:
        st.dataframe(violation_rows, hide_index=True)
    else:
        st.success("Every entry passed the segregation-of-duties control.")

    st.subheader("Drafted deficiency narratives")
    if report.narratives_skipped_reason:
        st.info(f"Narratives skipped: `{report.narratives_skipped_reason}`.")
    else:
        for n in report.narratives:
            st.markdown(f"**{n.entry_id}** · `{n.violation_code}`")
            st.write(n.narrative)
            if n.remediation:
                st.caption(f"Remediation: {', '.join(n.remediation)}")
            if n.citations:
                for citation in n.citations:
                    st.write(f"- {citation}")
            else:
                st.caption("Ungrounded — no policy excerpt cited.")

    _render_approval(run.approval_request)
    _render_audit_log(audit_log)


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

    views = {
        "Reconciliation Agent": render_reconciliation,
        "VAT Treatment Agent": render_vat_treatment,
        "AP Invoice Agent": render_ap_invoice,
        "Close Agent (Variance Analysis)": render_close_variance,
        "Controls (SoD) Agent": render_controls_sod,
    }
    agent = st.sidebar.radio("Agent", list(views))
    views[agent]()


if __name__ == "__main__":
    main()
