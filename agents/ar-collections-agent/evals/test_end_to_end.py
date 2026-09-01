from datetime import date
from pathlib import Path

import pytest
from approvals import ApprovalQueue, Decision, Role
from audit_log import AuditLogStore
from knowledge import KnowledgeBase

from ar_collections_agent import DunningPolicy, run_ar_collections_analysis
from fakes import ExplodingClient, dunning_client, no_tool_call_client, refusal_client
from fixtures import ALL_DOCUMENTS, AS_OF, POLICY_CITATION, dunning_payload

INVOICES_DIR = Path(__file__).parent / "fixtures" / "open_invoices"

TS = iter(f"2026-09-01T{i // 60:02d}:{i % 60:02d}:00Z" for i in range(240))


def fixed_now():
    return next(TS)


@pytest.fixture
def knowledge_base():
    kb = KnowledgeBase()
    kb.ingest(ALL_DOCUMENTS)
    return kb


@pytest.fixture
def empty_knowledge_base():
    return KnowledgeBase()


@pytest.fixture
def audit_log(tmp_path):
    store = AuditLogStore(tmp_path / "audit.db")
    yield store
    store.close()


@pytest.fixture
def approval_queue(tmp_path, audit_log):
    queue = ApprovalQueue(tmp_path / "approvals.db", audit_log)
    yield queue
    queue.close()


def _one_file_folder(tmp_path, name):
    folder = tmp_path / "open_invoices"
    folder.mkdir(exist_ok=True)
    (folder / f"{name}.csv").write_text((INVOICES_DIR / f"{name}.csv").read_text())
    return folder


def _run(folder, client, knowledge_base, audit_log, approval_queue, policy=None):
    return run_ar_collections_analysis(
        source_system="larenthia_trading",
        invoices_folder=folder,
        knowledge_base=knowledge_base,
        audit_log=audit_log,
        approval_queue=approval_queue,
        as_of_date=AS_OF,
        client=client,
        policy=policy,
        now=fixed_now,
    )


MIXED_FLAGGED = [
    ("INV-6004", "firm"),
    ("INV-6006", "reminder"),
    ("INV-6005", "reminder"),
    ("INV-6003", "reminder"),
]
SEVERE_FLAGGED = [
    ("INV-7001", "formal"),
    ("INV-7002", "formal"),
    ("INV-7003", "formal"),
    ("INV-7004", "firm"),
]


def test_current_book_submits_without_calling_the_model(
    tmp_path, knowledge_base, audit_log, approval_queue
):
    folder = _one_file_folder(tmp_path, "current_book")
    run = _run(folder, ExplodingClient(), knowledge_base, audit_log, approval_queue)

    assert run.report.flagged == []
    assert run.report.drafts == []
    assert run.report.drafts_skipped_reason == "no_flagged_invoices"
    assert run.draft_result is None
    assert run.approval_request.status == "pending"
    assert run.approval_request.current_stage == Role.REVIEWER.value

    actions = [e.action for e in audit_log.get_all()]
    assert actions == [
        "open_invoices_retrieved",
        "aging_computed",
        "dunning_drafts_skipped",
        "collections_report_generated",
        "approval_submitted:collections_report",
    ]

    buckets = {ia.invoice_id: ia.bucket for ia in run.report.invoice_agings}
    assert buckets["INV-5001"] == "current"
    assert buckets["INV-5002"] == "1-30"
    skipped = next(e for e in audit_log.get_all() if e.action == "dunning_drafts_skipped")
    assert skipped.output["reason"] == "no_flagged_invoices"
    assert audit_log.verify_chain().ok is True


def test_mixed_book_flows_to_approved_with_cited_drafts(
    tmp_path, knowledge_base, audit_log, approval_queue
):
    folder = _one_file_folder(tmp_path, "mixed_aging")
    run = _run(
        folder,
        dunning_client(dunning_payload(MIXED_FLAGGED)),
        knowledge_base, audit_log, approval_queue,
    )

    assert len(run.report.invoice_agings) == 6
    assert [(ia.invoice_id, ia.tone_tier) for ia in run.report.flagged] == MIXED_FLAGGED

    repeat_only = next(ia for ia in run.report.flagged if ia.invoice_id == "INV-6005")
    assert repeat_only.bucket == "1-30"
    assert any("overdue invoices" in r for r in repeat_only.flag_reasons)

    assert run.report.drafts_skipped_reason is None
    assert [d.invoice_id for d in run.report.drafts] == [k for k, _ in MIXED_FLAGGED]
    assert all(d.citations == [POLICY_CITATION] for d in run.report.drafts)
    assert run.report.model == "claude-sonnet-5"

    summary = run.report.to_dict()["summary"]
    assert summary["flagged_count"] == 4
    assert summary["tone_breakdown"] == {"firm": 1, "reminder": 3}
    assert summary["customers_flagged"] == [
        "Ashford Freight", "Halvar Logistics", "Kestrel Media"
    ]

    request = run.approval_request
    assert request.preparer == "ar-collections-agent"
    assert request.payload["summary"]["flagged_count"] == 4

    reviewed = approval_queue.decide(
        request.id, actor="alice", role=Role.REVIEWER, decision=Decision.APPROVE,
        timestamp=fixed_now(),
    )
    assert reviewed.current_stage == Role.APPROVER.value
    approved = approval_queue.decide(
        request.id, actor="bob", role=Role.APPROVER, decision=Decision.APPROVE,
        timestamp=fixed_now(),
    )
    assert approved.status == "approved"

    actions = [e.action for e in audit_log.get_all()]
    assert actions == [
        "open_invoices_retrieved",
        "aging_computed",
        "dunning_context_retrieved",
        "dunning_drafts_drafted",
        "collections_report_generated",
        "approval_submitted:collections_report",
        "approval_reviewer_approve:collections_report",
        "approval_approver_approve:collections_report",
    ]

    computed = next(e for e in audit_log.get_all() if e.action == "aging_computed")
    assert computed.output["flagged_count"] == 4
    flagged_ids = [f["invoice_id"] for f in computed.output["flagged"]]
    assert flagged_ids == [k for k, _ in MIXED_FLAGGED]
    assert all(f["flag_reasons"] for f in computed.output["flagged"])

    drafted = next(e for e in audit_log.get_all() if e.action == "dunning_drafts_drafted")
    assert drafted.model == "claude-sonnet-5"
    assert drafted.prompt_hash == run.report.draft_prompt_hash
    assert POLICY_CITATION in drafted.output["citations"]

    assert audit_log.verify_chain().ok is True


def test_severe_book_assigns_formal_tone_past_90_days(
    tmp_path, knowledge_base, audit_log, approval_queue
):
    folder = _one_file_folder(tmp_path, "severe_delinquency")
    run = _run(
        folder,
        dunning_client(dunning_payload(SEVERE_FLAGGED)),
        knowledge_base, audit_log, approval_queue,
    )

    assert [(ia.invoice_id, ia.tone_tier) for ia in run.report.flagged] == SEVERE_FLAGGED

    summary = run.report.to_dict()["summary"]
    assert summary["bucket_breakdown"]["90+"]["count"] == 3
    assert summary["bucket_breakdown"]["current"]["count"] == 1
    assert summary["tone_breakdown"] == {"formal": 3, "firm": 1}

    pinnacle = next(ia for ia in run.report.flagged if ia.invoice_id == "INV-7001")
    assert pinnacle.days_since_last_payment == (AS_OF - date(2026, 5, 15)).days
    assert audit_log.verify_chain().ok is True


def test_model_refusal_still_submits_report_without_drafts(
    tmp_path, knowledge_base, audit_log, approval_queue
):
    folder = _one_file_folder(tmp_path, "mixed_aging")
    run = _run(folder, refusal_client(category="cyber"), knowledge_base, audit_log, approval_queue)

    assert run.report.drafts == []
    assert run.report.drafts_skipped_reason == "dunning_drafts_refused"
    assert run.approval_request.status == "pending"

    refused = next(e for e in audit_log.get_all() if e.action == "dunning_drafts_refused")
    assert refused.output["refusal_category"] == "cyber"
    assert refused.prompt_hash == run.report.draft_prompt_hash

    actions = [e.action for e in audit_log.get_all()]
    assert "collections_report_generated" in actions
    assert "approval_submitted:collections_report" in actions
    assert audit_log.verify_chain().ok is True


def test_unparseable_model_response_still_submits_report(
    tmp_path, knowledge_base, audit_log, approval_queue
):
    folder = _one_file_folder(tmp_path, "mixed_aging")
    run = _run(folder, no_tool_call_client(), knowledge_base, audit_log, approval_queue)

    assert run.report.drafts == []
    assert run.report.drafts_skipped_reason == "dunning_drafts_failed"
    failed = next(e for e in audit_log.get_all() if e.action == "dunning_drafts_failed")
    assert "no record_dunning_drafts tool call" in failed.output["parse_error"]
    assert run.approval_request.status == "pending"
    assert audit_log.verify_chain().ok is True


def test_no_relevant_knowledge_still_drafts_ungrounded_emails(
    tmp_path, empty_knowledge_base, audit_log, approval_queue
):
    folder = _one_file_folder(tmp_path, "mixed_aging")
    run = _run(
        folder,
        dunning_client(dunning_payload(MIXED_FLAGGED, citation=None)),
        empty_knowledge_base, audit_log, approval_queue,
    )

    assert run.report.draft_citations == []
    assert run.report.drafts_skipped_reason is None
    assert all(d.citations == [] for d in run.report.drafts)

    context = next(e for e in audit_log.get_all() if e.action == "dunning_context_retrieved")
    assert context.output["grounded"] is False
    assert context.output["chunk_ids"] == []
    assert audit_log.verify_chain().ok is True


def test_reviewer_can_reject_the_report(tmp_path, knowledge_base, audit_log, approval_queue):
    folder = _one_file_folder(tmp_path, "mixed_aging")
    run = _run(
        folder,
        dunning_client(dunning_payload(MIXED_FLAGGED)),
        knowledge_base, audit_log, approval_queue,
    )

    rejected = approval_queue.decide(
        run.approval_request.id, actor="alice", role=Role.REVIEWER,
        decision=Decision.REJECT, timestamp=fixed_now(),
        comment="Halvar dispute open — hold INV-6005",
    )
    assert rejected.status == "rejected"
    assert audit_log.verify_chain().ok is True


def test_repeat_customer_rule_off_narrows_the_flagged_set(
    tmp_path, knowledge_base, audit_log, approval_queue
):
    folder = _one_file_folder(tmp_path, "mixed_aging")
    run = _run(
        folder,
        dunning_client(dunning_payload([("INV-6004", "firm"), ("INV-6006", "reminder"), ("INV-6003", "reminder")])),
        knowledge_base, audit_log, approval_queue,
        policy=DunningPolicy(flag_repeat_customers=False),
    )
    # INV-6005 (20 days, only pulled in by the repeat rule) is no longer flagged
    assert [ia.invoice_id for ia in run.report.flagged] == ["INV-6004", "INV-6006", "INV-6003"]
    assert audit_log.verify_chain().ok is True


def test_empty_folder_is_rejected(tmp_path, knowledge_base, audit_log, approval_queue):
    folder = tmp_path / "open_invoices"
    folder.mkdir()
    with pytest.raises(ValueError, match="no open invoices"):
        _run(folder, ExplodingClient(), knowledge_base, audit_log, approval_queue)


def test_mixed_currency_is_rejected(tmp_path, knowledge_base, audit_log, approval_queue):
    folder = tmp_path / "open_invoices"
    folder.mkdir()
    (folder / "inv.csv").write_text(
        "invoice_id,customer,invoice_date,due_date,amount,currency,last_payment_date\n"
        "INV-1,Acme,2026-06-01,2026-07-01,100.00,USD,\n"
        "INV-2,Beta,2026-06-02,2026-07-02,120.00,EUR,\n"
    )
    with pytest.raises(ValueError, match="currenc"):
        _run(folder, ExplodingClient(), knowledge_base, audit_log, approval_queue)


def test_malformed_csv_raises_before_any_approval(tmp_path, knowledge_base, audit_log, approval_queue):
    folder = tmp_path / "open_invoices"
    folder.mkdir()
    (folder / "inv.csv").write_text(
        "invoice_id,customer,invoice_date,due_date,amount,currency,last_payment_date\n"
        "INV-1,Acme,2026-06-01,2026-07-01,not-a-number,USD,\n"
    )
    from connectors import ConnectorParseError

    with pytest.raises(ConnectorParseError):
        _run(folder, ExplodingClient(), knowledge_base, audit_log, approval_queue)
    assert approval_queue.list_pending() == []
