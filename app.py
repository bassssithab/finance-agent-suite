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
  * ar-collections-agent : run_ar_collections_analysis() — deterministic
                           invoice aging (days overdue + bucket) with
                           configurable dunning-flagging rules, and cited
                           tone-escalating dunning-email drafts for the
                           flagged invoices. Real Claude API calls (only when
                           something is flagged); same key. No email is sent.
  * expense-agent        : check_receipt_policy_compliance() — vision
                           extraction of a receipt image, a deterministic
                           check against a configurable ExpensePolicy
                           (category limits, receipt age, required fields),
                           and a cited explanation of any violation. Real
                           Claude API calls; same key.
  * fpa-agent            : run_driver_based_forecast() — deterministic
                           driver-based projection of historical actuals with
                           configurable per-category growth assumptions,
                           high-sensitivity flagging, and a forecast narrative
                           that frames assumptions as assumptions. Always
                           makes a real Claude API call; same key.
  * tax-compliance-agent : run_vat_provision() — deterministic period-end VAT
                           provision (output VAT, input VAT, net payable /
                           refundable) by treatment category, anomaly
                           flagging, and a filing-support narrative that never
                           claims the return is ready to file. Always makes a
                           real Claude API call; same key.

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
    ROOT / "agents" / "ar-collections-agent",
    ROOT / "agents" / "expense-agent",
    ROOT / "agents" / "fpa-agent",
    ROOT / "agents" / "tax-compliance-agent",
    PLATFORM / "connectors",
    PLATFORM / "approvals",
    PLATFORM / "audit-log",
    PLATFORM / "knowledge",
):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from ap_agent import process_invoice  # noqa: E402
from approvals import ApprovalQueue  # noqa: E402
from ar_collections_agent import DunningPolicy, run_ar_collections_analysis  # noqa: E402
from audit_log import AuditLogStore  # noqa: E402
from close_agent import FlagThresholds, run_close_variance_analysis  # noqa: E402
from connectors import ConnectorParseError, FileDocumentConnector  # noqa: E402
from controls_sox_agent import ControlPolicy, run_journal_entry_control_test  # noqa: E402
from expense_agent import ExpensePolicy, check_receipt_policy_compliance  # noqa: E402
from fpa_agent import ForecastAssumptions, run_driver_based_forecast  # noqa: E402
from knowledge import KnowledgeBase  # noqa: E402
from reconciliation_agent import run_reconciliation  # noqa: E402
from tax_compliance_agent import run_vat_provision  # noqa: E402
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
_AR_COLLECTIONS_FIXTURES = _load_fixture_module(
    "ar_collections_agent_evals_fixtures", "ar-collections-agent"
)
AR_COLLECTIONS_POLICY_DOCS = _AR_COLLECTIONS_FIXTURES.ALL_DOCUMENTS
# The date the committed sample books are designed around — default the view's
# "as of date" to it so the sample-picker labels (31-60, 90+, …) hold exactly.
AR_COLLECTIONS_AS_OF = _AR_COLLECTIONS_FIXTURES.AS_OF
_EXPENSE_FIXTURES = _load_fixture_module("expense_agent_evals_fixtures", "expense-agent")
EXPENSE_POLICY_DOCS = _EXPENSE_FIXTURES.ALL_DOCUMENTS
# The date the committed sample receipts are aged against — default the view's
# "as of date" to it so the sample-picker labels ("~5 months old") hold.
EXPENSE_AS_OF = _EXPENSE_FIXTURES.AS_OF
FPA_METHODOLOGY_DOCS = _load_fixture_module(
    "fpa_agent_evals_fixtures", "fpa-agent"
).ALL_DOCUMENTS
TAX_VAT_POLICY_DOCS = _load_fixture_module(
    "tax_compliance_agent_evals_fixtures", "tax-compliance-agent"
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

AR_INVOICES_DIR = (
    ROOT / "agents" / "ar-collections-agent" / "evals" / "fixtures" / "open_invoices"
)
AR_SAMPLE_BOOKS = {
    "Current book — every invoice current or 1-30 days (nothing flagged, no Claude call)":
        "current_book.csv",
    "Mixed aging — a 31-60 reminder, a 61-90 firm notice, and a repeat-offender "
    "customer pulled in on a fresher invoice": "mixed_aging.csv",
    "Severe delinquency — three 90+ formal notices from one customer (who also "
    "trips the repeat rule), plus a 61-90 firm notice": "severe_delinquency.csv",
}

EXPENSE_RECEIPT_DIR = (
    ROOT / "agents" / "expense-agent" / "evals" / "fixtures" / "receipts"
)
EXPENSE_SAMPLE_RECEIPTS = {
    "Compliant taxi — $38.40 fare within the taxi cap, a few days old "
    "(passes; no explanation call)": "compliant_taxi.png",
    "Over-limit dinner — $182.50 meal, well over the $75 per-meal cap "
    "(category_over_limit + cited explanation)": "over_limit_dinner.png",
    "Stale hotel — $212 room within the lodging cap but ~5 months old "
    "(receipt_too_old)": "stale_hotel.png",
}

FPA_ACTUALS_DIR = ROOT / "agents" / "fpa-agent" / "evals" / "fixtures" / "actuals"

TAX_TXN_DIR = (
    ROOT / "agents" / "tax-compliance-agent" / "evals" / "fixtures" / "transactions"
)
TAX_SAMPLE_PERIODS = {
    "Normal payable — standard-rated sales & purchases, a zero-rated export, an "
    "out-of-scope drop-ship (net payable $14,550.00, no anomalies)": "normal_payable.csv",
    "Refundable period — a slow sales month against large standard-rated capex "
    "(net refundable −$14,700.00 → net_refundable_position)": "refundable_period.csv",
    "Data-quality issues — a standard-rated sale with no rate, a zero-rated sale "
    "carrying a rate, a 'reduced-rated' treatment, a 'refund' type (4 anomalies; "
    "2 transactions excluded from totals)": "data_quality_issues.csv",
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
# AR collections agent view
# --------------------------------------------------------------------------
def render_ar_collections() -> None:
    st.header("AR Collections Agent")
    st.write(
        "Ages every open invoice in **deterministic** code (days overdue + "
        "aging bucket), flags the invoices that warrant a collection action "
        "against configurable rules, retrieves from `platform/knowledge` to "
        "draft a dunning email for each flagged invoice **whose tone escalates "
        "with how overdue it is** (gentle reminder → firm → formal), then "
        "submits the whole report for human approval. `autonomy: draft_only` — "
        "the agent never sends an email, and it never does the arithmetic."
    )
    st.caption(
        "The collections-policy corpus is a **synthetic test fixture** "
        "(\"Larenthia Trading Co\", the same fictional entity as the other "
        "agents) — not a real collections policy. It exists to exercise "
        "retrieval, citation, and the grounded-vs-ungrounded draft paths."
    )

    api_key = _get_api_key()
    if not api_key:
        _missing_key_warning(
            "The AR collections agent makes a real Claude API call to draft "
            "dunning emails (a book with nothing flagged makes no call),"
        )

    col_days, col_amt = st.columns(2)
    min_days = col_days.number_input(
        "Minimum days overdue — flag any invoice at or past this many days overdue",
        min_value=1, value=31, step=1,
    )
    min_amt = col_amt.number_input(
        "Minimum balance to chase ($) — skip smaller balances (0 = disabled)",
        min_value=0, value=0, step=100,
    )

    flag_repeat = st.checkbox(
        "Also flag repeat-offender customers — every overdue invoice of a "
        "customer with several overdue invoices",
        value=True,
    )
    repeat_min = st.number_input(
        "…how many overdue invoices makes a customer a repeat offender",
        min_value=2, value=2, step=1, disabled=not flag_repeat,
    )

    policy = DunningPolicy(
        min_days_overdue=int(min_days),
        flag_repeat_customers=flag_repeat,
        repeat_customer_min_overdue_invoices=int(repeat_min),
        min_amount=Decimal(str(min_amt)) if min_amt > 0 else None,
    )

    as_of_date = st.date_input("As of date — the aging is computed against this", value=AR_COLLECTIONS_AS_OF)

    source = st.radio(
        "Open invoices",
        ["Use a committed sample book", "Upload my own open-invoices CSV"],
        horizontal=True,
    )

    invoices_upload = None
    if source == "Upload my own open-invoices CSV":
        invoices_upload = st.file_uploader("Open-invoices CSV", type="csv")
        st.caption(
            "Schema: `invoice_id,customer,invoice_date,due_date,amount,currency,"
            "last_payment_date` (`currency` and `last_payment_date` optional). "
            "All invoices must share one currency."
        )
    else:
        label = st.radio("Sample book", list(AR_SAMPLE_BOOKS), index=1)
        sample_file = AR_SAMPLE_BOOKS[label]
        st.caption(
            "Sample books are fictional open invoices from the "
            "ar-collections-agent eval suite "
            "(`agents/ar-collections-agent/evals/fixtures/open_invoices/`) — not "
            f"real receivables. They are built around an as-of date of "
            f"{AR_COLLECTIONS_AS_OF.isoformat()}; change the date above and the "
            "buckets shift accordingly."
        )

    if not st.button("Run aging + dunning drafts", type="primary", disabled=not api_key):
        return

    import anthropic

    with tempfile.TemporaryDirectory() as tmp:
        invoices_folder = Path(tmp) / "open_invoices"
        invoices_folder.mkdir()

        if source == "Upload my own open-invoices CSV":
            if invoices_upload is None:
                st.error("Upload an open-invoices CSV first.")
                return
            (invoices_folder / "upload.csv").write_bytes(invoices_upload.getvalue())
        else:
            (invoices_folder / sample_file).write_text(
                (AR_INVOICES_DIR / sample_file).read_text()
            )

        kb = KnowledgeBase()
        kb.ingest(AR_COLLECTIONS_POLICY_DOCS)
        audit_log, approval_queue = new_stores()
        client = anthropic.Anthropic(api_key=api_key)

        try:
            with st.spinner("Aging the invoices and drafting dunning emails via Claude…"):
                run = run_ar_collections_analysis(
                    source_system="demo_co",
                    invoices_folder=invoices_folder,
                    knowledge_base=kb,
                    audit_log=audit_log,
                    approval_queue=approval_queue,
                    as_of_date=as_of_date,
                    client=client,
                    policy=policy,
                )
            _render_ar_collections_result(run, audit_log)
        except anthropic.AnthropicError as exc:
            st.error(f"Claude API call failed: {exc}")
        except (ValueError, ConnectorParseError) as exc:
            st.error(f"Could not run the analysis: {exc}")
        finally:
            audit_log.close()
            approval_queue.close()


def _render_ar_collections_result(run, audit_log) -> None:
    report = run.report
    summary = report.summary()

    st.caption(
        f"As of {report.as_of_date} · model `{report.model or '—'}` · draft for "
        "a reviewer — no dunning email is sent"
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Open invoices", summary["invoice_count"])
    c2.metric("Overdue", summary["overdue_count"])
    c3.metric("Flagged", summary["flagged_count"])
    c4.metric("Currency", report.currency)
    c5, c6 = st.columns(2)
    c5.metric("Total open", money(Decimal(summary["total_open"])))
    c6.metric("Total overdue", money(Decimal(summary["total_overdue"])))

    st.subheader("Aging buckets")
    st.dataframe(
        [
            {
                "bucket": bucket,
                "invoices": v["count"],
                "amount": money(Decimal(v["amount"])),
            }
            for bucket, v in summary["bucket_breakdown"].items()
        ],
        hide_index=True,
    )
    if summary["tone_breakdown"]:
        st.write(
            "Dunning tone of the flagged invoices: "
            + ", ".join(
                f"`{tone}` × {count}"
                for tone, count in sorted(summary["tone_breakdown"].items())
            )
        )

    st.subheader("Invoice aging")
    st.dataframe(
        [
            {
                "": "🚩" if ia.flagged else "",
                "invoice": ia.invoice_id,
                "customer": ia.customer,
                "invoice date": ia.invoice_date,
                "due date": ia.due_date,
                "amount": money(ia.amount),
                "days overdue": ia.days_overdue,
                "bucket": ia.bucket,
                "days since last payment": (
                    "—" if ia.days_since_last_payment is None
                    else ia.days_since_last_payment
                ),
                "tone tier": ia.tone_tier or "—",
            }
            for ia in report.invoice_agings
        ],
        hide_index=True,
    )

    st.subheader("Flagged invoices")
    if report.flagged:
        for ia in report.flagged:
            st.markdown(
                f"**{ia.customer} · {ia.invoice_id}** — {ia.bucket} · "
                f"tone: `{ia.tone_tier}`"
            )
            for reason in ia.flag_reasons:
                st.write(f"- {reason}")
    else:
        st.success(
            "Nothing warranted a collection action — no dunning emails drafted."
        )

    st.subheader("Drafted dunning emails")
    if report.drafts_skipped_reason:
        st.info(f"Dunning drafts skipped: `{report.drafts_skipped_reason}`.")
    else:
        for d in report.drafts:
            st.markdown(f"**{d.invoice_id}** · `{d.tone}`")
            st.markdown(f"**Subject:** {d.subject}")
            st.text(d.body)
            if d.citations:
                for citation in d.citations:
                    st.write(f"- {citation}")
            else:
                st.caption("Ungrounded — no policy excerpt cited.")

    _render_approval(run.approval_request)
    _render_audit_log(audit_log)


# --------------------------------------------------------------------------
# Expense agent view
# --------------------------------------------------------------------------
def render_expense() -> None:
    st.header("Expense Agent")
    st.write(
        "Extracts a receipt image via Claude vision, runs a **deterministic** "
        "check against a configurable expense policy in plain code — per-category "
        "spending limits, a maximum receipt age, required fields present — "
        "retrieves from `platform/knowledge` to draft a cited explanation of any "
        "flagged violation, then submits the extracted expense plus the "
        "compliance result for human approval. The model only transcribes and "
        "infers a category — it never decides whether the expense is in policy. "
        "`autonomy: draft_only`."
    )
    st.caption(
        "The expense-policy corpus is a **synthetic test fixture** (\"Larenthia "
        "Trading Co\", the same fictional entity as the other agents) — not a "
        "real T&E policy. It exists to exercise retrieval, citation, and the "
        "grounded-vs-ungrounded explanation paths."
    )

    api_key = _get_api_key()
    if not api_key:
        _missing_key_warning(
            "The expense agent makes a real Claude API call to extract the "
            "receipt (and, when something is flagged and policy is on file, to "
            "explain it),"
        )

    col_m, col_t, col_l = st.columns(3)
    meals_limit = col_m.number_input(
        "Meals limit ($) — per receipt (0 = no meals cap)", min_value=0.0, value=75.0, step=5.0
    )
    travel_limit = col_t.number_input(
        "Travel — taxi limit ($) (0 = no taxi cap)", min_value=0.0, value=60.0, step=5.0
    )
    lodging_limit = col_l.number_input(
        "Lodging limit ($) (0 = no lodging cap)", min_value=0.0, value=250.0, step=10.0
    )
    max_age = st.number_input(
        "Maximum receipt age (days) — flag receipts older than this (0 = disabled)",
        min_value=0, value=90, step=1,
    )
    st.caption(
        "The sample receipts carry the categories **Meals**, **Travel - taxi** "
        "and **Lodging** (matched case-insensitively against the limits above)."
    )

    category_limits = {
        key: Decimal(str(value))
        for key, value in (
            ("meals", meals_limit),
            ("travel - taxi", travel_limit),
            ("lodging", lodging_limit),
        )
        if value > 0
    }
    policy = ExpensePolicy(
        category_limits=category_limits,
        max_receipt_age_days=int(max_age) if max_age > 0 else None,
    )

    as_of_date = st.date_input(
        "As of date — the receipt-age check runs against this", value=EXPENSE_AS_OF
    )

    source = st.radio(
        "Receipt",
        ["Use a committed sample receipt", "Upload a receipt image"],
        horizontal=True,
    )

    document_bytes = None
    document_id = None
    if source == "Upload a receipt image":
        upload = st.file_uploader("Receipt image", type=["png", "jpg", "jpeg"])
        if upload is not None:
            document_bytes = upload.getvalue()
            document_id = "upload" + Path(upload.name).suffix.lower()
    else:
        label = st.radio("Sample receipt", list(EXPENSE_SAMPLE_RECEIPTS), index=0)
        document_id = EXPENSE_SAMPLE_RECEIPTS[label]
        st.caption(
            "Sample receipts are fictional test fixtures generated by the eval "
            "suite (`agents/expense-agent/evals/generate_sample_receipts.py`), "
            f"each stamped \"SAMPLE — NOT A REAL RECEIPT\" and aged against "
            f"{EXPENSE_AS_OF.isoformat()} — not real merchant documents."
        )

    if not st.button("Check receipt", type="primary", disabled=not api_key):
        return

    if source == "Upload a receipt image" and document_bytes is None:
        st.error("Upload a PNG or JPEG receipt image first.")
        return

    import anthropic

    kb = KnowledgeBase()
    kb.ingest(EXPENSE_POLICY_DOCS)
    audit_log, approval_queue = new_stores()
    client = anthropic.Anthropic(api_key=api_key)

    with tempfile.TemporaryDirectory() as tmp:
        if source == "Upload a receipt image":
            folder = Path(tmp)
            (folder / document_id).write_bytes(document_bytes)
        else:
            folder = EXPENSE_RECEIPT_DIR

        connector = FileDocumentConnector(source_system="demo_co", folder=folder)

        try:
            with st.spinner("Extracting the receipt and checking it against policy via Claude…"):
                run = check_receipt_policy_compliance(
                    document_id=document_id,
                    document_connector=connector,
                    knowledge_base=kb,
                    audit_log=audit_log,
                    approval_queue=approval_queue,
                    policy=policy,
                    as_of_date=as_of_date,
                    client=client,
                )
            _render_expense_result(run, audit_log)
        except anthropic.AnthropicError as exc:
            st.error(f"Claude API call failed: {exc}")
        except (ValueError, ConnectorParseError) as exc:
            st.error(f"Could not run the check: {exc}")
        finally:
            audit_log.close()
            approval_queue.close()


def _render_expense_result(run, audit_log) -> None:
    if run.draft is None:
        extraction = run.extraction
        if extraction.refused:
            st.error(
                f"The model refused to extract the receipt (category: "
                f"`{extraction.refusal_category}`). No approval request was submitted."
            )
        else:
            st.error(
                f"Extraction failed: {extraction.parse_error}. No approval "
                "request was submitted."
            )
        _render_audit_log(audit_log)
        return

    draft = run.draft
    r = draft.receipt

    st.subheader("Extracted receipt")
    c1, c2, c3 = st.columns(3)
    c1.metric("Vendor", r.vendor or "—")
    c2.metric("Date", r.date or "—")
    c3.metric("Amount", f"{r.currency} {r.amount}".strip())
    c4, c5, c6 = st.columns(3)
    c4.metric("Currency", r.currency or "—")
    c5.metric("Expense category", r.expense_category or "—")
    c6.metric("Extraction confidence", f"{r.extraction_confidence:.2f}")
    st.caption(
        f"Model: `{draft.model}` · category is the model's inference · draft for "
        "a reviewer, not a reimbursement decision"
    )

    st.subheader("Deterministic policy check")
    c = draft.compliance
    if draft.compliance_flagged:
        st.error(
            "**POLICY VIOLATION FLAGGED.** The draft is still submitted for "
            "approval (the agent drafts; it doesn't get to block), carrying this flag."
        )
        st.dataframe(
            [
                {
                    "code": v.code,
                    "field": v.field,
                    "detail (deterministic)": v.detail,
                }
                for v in c.violations
            ],
            hide_index=True,
        )
    else:
        st.success("Within policy — the deterministic check found no violations.")
    st.caption(
        f"Receipt date parsed as {c.parsed_date or '—'} · limit applied "
        f"{money(c.applied_limit) if c.applied_limit is not None else '—'} · "
        f"checked as of {c.as_of_date}"
    )

    st.subheader("Drafted policy explanation")
    if draft.explanation_skipped_reason:
        st.info(f"Explanation skipped: `{draft.explanation_skipped_reason}`.")
    else:
        for e in draft.explanations:
            st.markdown(f"**`{e.code}`**")
            st.write(e.explanation)
            if e.citations:
                for citation in e.citations:
                    st.write(f"- {citation}")
            else:
                st.caption("Ungrounded — no policy excerpt cited.")

    _render_approval(run.approval_request)
    _render_audit_log(audit_log)


# --------------------------------------------------------------------------
# FP&A agent — forecast view
# --------------------------------------------------------------------------
def render_fpa() -> None:
    st.header("FP&A Agent — Driver-Based Forecast")
    st.write(
        "Projects historical actuals forward in **deterministic** code — each "
        "line item compounded from its most recent actual by a per-category "
        "growth assumption (or a flat default) — flags any line whose assumed "
        "rate implies an unusually large period-over-period change, retrieves "
        "from `platform/knowledge` to draft a forecast narrative, then submits "
        "the whole forecast for human approval. The model writes only the "
        "narrative; it never does the projection arithmetic. `autonomy: "
        "draft_only`."
    )
    st.caption(
        "The FP&A-methodology corpus is a **synthetic test fixture** "
        "(\"Larenthia Trading Co\", the same fictional entity as the other "
        "agents) — not a real methodology. It exists to exercise retrieval, "
        "citation, and the grounded-vs-ungrounded narrative paths."
    )

    api_key = _get_api_key()
    if not api_key:
        _missing_key_warning(
            "The FP&A agent always makes a real Claude API call to draft the "
            "forecast narrative,"
        )

    col_g, col_t, col_h = st.columns(3)
    default_pct = col_g.number_input(
        "Default growth rate (% per period)", value=2.0, step=0.5
    )
    threshold_pct = col_t.number_input(
        "High-sensitivity threshold (% period-over-period)",
        min_value=0.0, value=25.0, step=5.0,
    )
    horizon = col_h.number_input(
        "Horizon (periods to project)", min_value=1, value=3, step=1
    )
    category_overrides = st.text_input(
        "Per-category growth overrides",
        value="Revenue: 30",
        help="Comma-separated `Category: percent-per-period`. Leave blank to "
        "apply the default rate to every line.",
    )
    st.caption(
        "Per-category overrides format: `Revenue: 30, Cost of sales: 1.5`. The "
        "sample actuals carry the categories **Revenue**, **Cost of sales** and "
        "**Operating expenses**."
    )

    source = st.radio(
        "Historical actuals",
        ["Use the committed sample actuals", "Upload my own"],
        horizontal=True,
    )

    actuals_upload = None
    if source == "Upload my own":
        actuals_upload = st.file_uploader("Historical actuals CSV", type="csv")
        st.caption(
            "One CSV containing every historical period. Schema: "
            "`period,account,line_item,category,amount,currency` (`category` and "
            "`currency` optional). Periods are monthly `YYYY-MM`; all rows must "
            "share one currency."
        )
    else:
        st.caption(
            "The committed sample is three fictional monthly CSVs from the "
            "fpa-agent eval suite (`agents/fpa-agent/evals/fixtures/actuals/` — "
            "`2026-04`, `2026-05`, `2026-06`), read together as one history. "
            "\"Software subscriptions\" drops out of the last month, so the "
            "carry-forward / `stale_base` flag fires on it."
        )

    if not st.button("Run forecast", type="primary", disabled=not api_key):
        return

    if source == "Upload my own" and actuals_upload is None:
        st.error("Upload a historical-actuals CSV first.")
        return

    category_growth = {}
    raw = category_overrides.strip()
    if raw:
        try:
            for part in raw.split(","):
                name, pct = part.split(":")
                category_growth[name.strip()] = Decimal(pct.strip()) / Decimal("100")
        except (ValueError, ArithmeticError):
            st.error(
                "Couldn't parse the per-category overrides — use "
                "`Category: percent`, comma-separated."
            )
            return

    assumptions = ForecastAssumptions(
        default_growth=Decimal(str(default_pct)) / Decimal("100"),
        category_growth=category_growth,
        max_pop_change_pct=Decimal(str(threshold_pct)) / Decimal("100"),
    )

    import anthropic

    kb = KnowledgeBase()
    kb.ingest(FPA_METHODOLOGY_DOCS)
    audit_log, approval_queue = new_stores()
    client = anthropic.Anthropic(api_key=api_key)

    with tempfile.TemporaryDirectory() as tmp:
        if source == "Upload my own":
            actuals_folder = Path(tmp) / "actuals"
            actuals_folder.mkdir()
            (actuals_folder / "upload.csv").write_bytes(actuals_upload.getvalue())
        else:
            actuals_folder = FPA_ACTUALS_DIR

        try:
            with st.spinner("Projecting the forecast and drafting the narrative via Claude…"):
                run = run_driver_based_forecast(
                    source_system="demo_co",
                    actuals_folder=actuals_folder,
                    knowledge_base=kb,
                    audit_log=audit_log,
                    approval_queue=approval_queue,
                    assumptions=assumptions,
                    horizon=int(horizon),
                    client=client,
                )
            _render_fpa_result(run, audit_log)
        except anthropic.AnthropicError as exc:
            st.error(f"Claude API call failed: {exc}")
        except (ValueError, ConnectorParseError) as exc:
            st.error(f"Could not run the forecast: {exc}")
        finally:
            audit_log.close()
            approval_queue.close()


def _render_fpa_result(run, audit_log) -> None:
    report = run.report
    summary = report.summary()

    st.warning(
        "**This is a projection, not a prediction.** Every figure below is "
        "`base × (1 + assumed rate)^periods` and holds only if the assumed "
        "growth rates hold. The growth rates are inputs the planner chose, not "
        "measured trends."
    )
    st.caption(
        f"Base period {report.base_period} · projecting "
        f"{', '.join(report.projected_periods)} · model `{report.model or '—'}` "
        "· draft for a reviewer, not a committed plan"
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Base period", report.base_period)
    c2.metric("Horizon", report.horizon)
    c3.metric("Currency", report.currency)
    c4.metric("Flagged rows", summary["flagged_count"])
    c5, c6, c7 = st.columns(3)
    c5.metric("Total base", money(Decimal(summary["total_base"])))
    c6.metric(
        f"Total projected ({report.projected_periods[-1]})",
        money(Decimal(summary["total_projected_final"])),
    )
    growth = summary["total_growth_pct_over_horizon"]
    c7.metric(
        "Total growth over horizon",
        "n/a" if growth is None else f"{Decimal(growth) * 100:.1f}%",
    )

    st.subheader("By category")
    st.dataframe(
        [
            {
                "category": category,
                "base": money(Decimal(v["base"])),
                f"projected ({report.projected_periods[-1]})": money(Decimal(v["final"])),
                "growth over horizon": (
                    "n/a" if v["growth_pct_over_horizon"] is None
                    else f"{Decimal(v['growth_pct_over_horizon']) * 100:.1f}%"
                ),
            }
            for category, v in summary["by_category"].items()
        ],
        hide_index=True,
    )

    st.subheader("Projection")
    by_line: dict = {}
    for pl in report.projected_lines:
        key = (pl.account, pl.line_item)
        row = by_line.setdefault(
            key,
            {
                "": "🚩" if pl.flagged else "",
                "account": pl.account,
                "line item": pl.line_item,
                "category": pl.category or "—",
                "base": money(pl.base_amount),
                "base period": pl.base_period,
                "assumed rate / period": f"{pl.growth_rate * 100:.1f}%",
                "source": pl.growth_source,
            },
        )
        row[pl.period] = money(pl.projected_amount)
        if pl.flagged:
            row[""] = "🚩"
    ordered = (
        ["", "account", "line item", "category", "base", "base period"]
        + list(report.projected_periods)
        + ["assumed rate / period", "source"]
    )
    st.dataframe(
        [{col: row.get(col, "") for col in ordered} for row in by_line.values()],
        hide_index=True,
    )

    st.subheader("Flagged lines")
    if report.flagged:
        flagged_by_line: dict = {}
        for pl in report.flagged:
            entry = flagged_by_line.setdefault(
                (pl.account, pl.line_item),
                {"rate": pl.growth_rate, "source": pl.growth_source, "reasons": []},
            )
            for reason in pl.flag_reasons:
                if reason not in entry["reasons"]:
                    entry["reasons"].append(reason)
        for (account, line_item), entry in flagged_by_line.items():
            st.markdown(
                f"**{account} · {line_item}** — assumed rate "
                f"{entry['rate'] * 100:.1f}% / period ({entry['source']})"
            )
            for reason in entry["reasons"]:
                st.write(f"- {reason}")
    else:
        st.success(
            "No line tripped the high-sensitivity, negative-projection, or "
            "stale-base checks."
        )

    st.subheader("Drafted forecast narrative")
    if report.narrative_skipped_reason:
        st.info(f"Narrative skipped: `{report.narrative_skipped_reason}`.")
    else:
        n = report.narrative
        st.markdown(n.summary)
        st.markdown("**Assumptions — stated as assumptions:**")
        for item in n.assumptions_described:
            st.write(f"- {item}")
        st.markdown("**Flagged high-sensitivity items called out:**")
        if n.flagged_items_called_out:
            for item in n.flagged_items_called_out:
                st.write(f"- {item}")
        else:
            st.write("_None — nothing was flagged as high-sensitivity._")
        if n.citations:
            st.markdown("**Citations:**")
            for citation in n.citations:
                st.write(f"- {citation}")
        else:
            st.caption("Ungrounded — no methodology excerpt cited.")

    _render_approval(run.approval_request)
    _render_audit_log(audit_log)


# --------------------------------------------------------------------------
# Tax compliance agent — VAT provision view
# --------------------------------------------------------------------------
def render_tax_compliance() -> None:
    st.header("Tax Compliance Agent — VAT Provision")
    st.write(
        "Computes output VAT (VAT on sales) and input VAT (VAT on purchases) by "
        "treatment category in **deterministic** code, nets them into the "
        "period's payable or refundable position, flags anomalies worth scrutiny "
        "(a net refund, an unrecognized VAT treatment, a treatment/rate "
        "mismatch), retrieves from `platform/knowledge` to draft a filing-support "
        "narrative, then submits the whole provision for human approval. The "
        "model writes only the narrative; it never does the arithmetic and never "
        "claims the return is ready to file. `autonomy: draft_only`."
    )
    st.caption(
        "The VAT corpus is a **synthetic test fixture** describing the same "
        "fictional jurisdiction (\"Larenthia\") the VAT Treatment agent "
        "classifies against — four categories, a 15% standard rate — not real "
        "VAT law. It exists to exercise retrieval, citation, and the "
        "grounded-vs-ungrounded narrative paths."
    )

    api_key = _get_api_key()
    if not api_key:
        _missing_key_warning(
            "The tax compliance agent always makes a real Claude API call to "
            "draft the filing-support narrative,"
        )

    source = st.radio(
        "Period transactions",
        ["Use a committed sample period", "Upload my own transaction CSV"],
        horizontal=True,
    )

    txn_upload = None
    if source == "Upload my own transaction CSV":
        txn_upload = st.file_uploader("VAT transaction CSV", type="csv")
        st.caption(
            "Schema: `transaction_id,date,transaction_type,amount,vat_treatment,"
            "vat_rate,currency` (`vat_treatment`, `vat_rate` and `currency` "
            "optional). `transaction_type` is `sale` or `purchase`; "
            "`vat_treatment` is one of standard-rated / zero-rated / exempt / "
            "out-of-scope; `vat_rate` applies only where standard-rated. All "
            "rows must share one currency."
        )
    else:
        label = st.radio("Sample period", list(TAX_SAMPLE_PERIODS), index=0)
        sample_file = TAX_SAMPLE_PERIODS[label]
        st.caption(
            "Sample periods are fictional transactions from the "
            "tax-compliance-agent eval suite "
            "(`agents/tax-compliance-agent/evals/fixtures/transactions/`), "
            "standard rate 0.15 — not real ledgers."
        )

    if not st.button("Run VAT provision", type="primary", disabled=not api_key):
        return

    import anthropic

    kb = KnowledgeBase()
    kb.ingest(TAX_VAT_POLICY_DOCS)
    audit_log, approval_queue = new_stores()
    client = anthropic.Anthropic(api_key=api_key)

    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / "vat_transactions"
        folder.mkdir()

        if source == "Upload my own transaction CSV":
            if txn_upload is None:
                st.error("Upload a VAT transaction CSV first.")
                return
            (folder / "upload.csv").write_bytes(txn_upload.getvalue())
        else:
            (folder / sample_file).write_text((TAX_TXN_DIR / sample_file).read_text())

        try:
            with st.spinner("Computing the VAT provision and drafting filing support via Claude…"):
                run = run_vat_provision(
                    source_system="demo_co",
                    transactions_folder=folder,
                    knowledge_base=kb,
                    audit_log=audit_log,
                    approval_queue=approval_queue,
                    client=client,
                )
            _render_tax_compliance_result(run, audit_log)
        except anthropic.AnthropicError as exc:
            st.error(f"Claude API call failed: {exc}")
        except (ValueError, ConnectorParseError) as exc:
            st.error(f"Could not run the provision: {exc}")
        finally:
            audit_log.close()
            approval_queue.close()


def _render_tax_compliance_result(run, audit_log) -> None:
    report = run.report
    summary = report.summary()

    st.warning(
        "**Filing support, not a filed return.** This is a draft provision for a "
        "reviewer — not a VAT return, not a confirmation the figures are complete "
        "or correct, and not tax advice. Whether the return can be filed is the "
        "reviewer's decision and ultimately a qualified tax professional's."
    )
    st.caption(
        f"Period {report.period_label} "
        f"({report.date_range['from']} → {report.date_range['to']}) · "
        f"{report.currency} · model `{report.model or '—'}`"
    )

    position = report.position
    if position == "payable":
        st.info(
            f"**Net VAT position: PAYABLE {money(report.net_vat)}** — output VAT "
            f"{money(report.output_vat_total)} less input VAT "
            f"{money(report.input_vat_total)}."
        )
    elif position == "refundable":
        st.warning(
            f"**Net VAT position: REFUNDABLE {money(report.net_vat)}** — input VAT "
            f"{money(report.input_vat_total)} exceeds output VAT "
            f"{money(report.output_vat_total)}. A net refund is unusual for a "
            "mostly standard-rated trader and warrants a second look."
        )
    else:
        st.info("**Net VAT position: NIL** — output VAT equals input VAT.")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Output VAT", money(report.output_vat_total))
    c2.metric("Input VAT", money(report.input_vat_total))
    c3.metric("Net VAT", money(report.net_vat))
    c4.metric("Position", position)

    st.subheader("By treatment category")
    rows = []
    for treatment, sides in report.by_treatment.items():
        for side, cell in sides.items():
            if cell["count"]:
                rows.append(
                    {
                        "treatment": treatment,
                        "side": side,
                        "transactions": cell["count"],
                        "net amount": money(cell["amount"]),
                        "VAT": money(cell["vat"]),
                    }
                )
    if rows:
        st.dataframe(rows, hide_index=True)
    else:
        st.write("No recognised transactions.")

    st.subheader("Anomalies")
    if report.anomalies:
        st.error(
            f"**{len(report.anomalies)} anomal"
            f"{'y' if len(report.anomalies) == 1 else 'ies'} flagged.** The "
            "provision is still submitted for approval, carrying these flags; "
            "each is for specialist review, not resolved here."
        )
        st.dataframe(
            [
                {
                    "code": a.code,
                    "transaction": a.transaction_id or "— (whole period)",
                    "reason (deterministic)": a.detail,
                }
                for a in report.anomalies
            ],
            hide_index=True,
        )
        excluded = summary["transactions_excluded_from_totals"]
        if excluded:
            st.caption(
                "Excluded from the VAT totals (could not be classified): "
                f"{', '.join(excluded)} — the net position may be understated "
                "until these are resolved."
            )
    else:
        st.success("No anomalies — the deterministic checks found nothing to flag.")

    st.subheader("Drafted filing-support narrative")
    if report.narrative_skipped_reason:
        st.info(f"Narrative skipped: `{report.narrative_skipped_reason}`.")
    else:
        n = report.narrative
        st.markdown(n.position_summary)
        if n.specialist_review_needed:
            st.error(
                "**Specialist review needed** — the narrative flags at least one "
                "item for a qualified tax specialist before filing."
            )
        else:
            st.caption(
                "The narrative does not flag a specialist review as needed — the "
                "reviewer still decides."
            )
        st.markdown("**Anomaly explanations:**")
        if n.anomaly_explanations:
            for item in n.anomaly_explanations:
                st.write(f"- {item}")
        else:
            st.write("_No anomalies to explain._")
        if n.citations:
            st.markdown("**Citations:**")
            for citation in n.citations:
                st.write(f"- {citation}")
        else:
            st.caption("Ungrounded — no filing-guidance excerpt cited.")

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
        "AR Collections Agent": render_ar_collections,
        "Expense Agent": render_expense,
        "FPA Agent (Forecast)": render_fpa,
        "Tax Compliance Agent (VAT Provision)": render_tax_compliance,
    }
    agent = st.sidebar.radio("Agent", list(views))
    views[agent]()


if __name__ == "__main__":
    main()
