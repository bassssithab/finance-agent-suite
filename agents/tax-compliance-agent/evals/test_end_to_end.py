from pathlib import Path

import pytest
from approvals import ApprovalQueue, Decision, Role
from audit_log import AuditLogStore
from knowledge import KnowledgeBase

from tax_compliance_agent import run_vat_provision
from fakes import ExplodingClient, narrative_client, no_tool_call_client, refusal_client
from fixtures import ALL_DOCUMENTS, FILING_CITATION, narrative_payload

TXN_DIR = Path(__file__).parent / "fixtures" / "transactions"

TS = iter(f"2026-07-31T{i // 60:02d}:{i % 60:02d}:00Z" for i in range(240))


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
    folder = tmp_path / "vat_transactions"
    folder.mkdir(exist_ok=True)
    (folder / f"{name}.csv").write_text((TXN_DIR / f"{name}.csv").read_text())
    return folder


def _run(folder, client, knowledge_base, audit_log, approval_queue, policy=None):
    return run_vat_provision(
        source_system="larenthia_trading",
        transactions_folder=folder,
        knowledge_base=knowledge_base,
        audit_log=audit_log,
        approval_queue=approval_queue,
        policy=policy,
        client=client,
        now=fixed_now,
    )


def test_normal_payable_flows_to_approved_with_a_grounded_narrative(
    tmp_path, knowledge_base, audit_log, approval_queue
):
    folder = _one_file_folder(tmp_path, "normal_payable")
    run = _run(
        folder, narrative_client(narrative_payload()),
        knowledge_base, audit_log, approval_queue,
    )

    report = run.report
    assert report.position == "payable"
    assert str(report.net_vat) == "14550.00"
    assert report.anomalies == []
    assert report.narrative_skipped_reason is None
    assert report.narrative.citations == [FILING_CITATION]
    assert report.model == "claude-sonnet-5"

    request = run.approval_request
    assert request.status == "pending"
    assert request.preparer == "tax-compliance-agent"
    assert request.payload["summary"]["position"] == "payable"
    assert request.payload["summary"]["net_vat"] == "14550.00"

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
        "vat_transactions_retrieved",
        "vat_provision_computed",
        "filing_guidance_context_retrieved",
        "filing_support_narrative_drafted",
        "vat_provision_report_generated",
        "approval_submitted:vat_provision",
        "approval_reviewer_approve:vat_provision",
        "approval_approver_approve:vat_provision",
    ]

    computed = next(e for e in audit_log.get_all() if e.action == "vat_provision_computed")
    assert computed.output["position"] == "payable"
    assert computed.output["anomaly_count"] == 0
    assert computed.output["by_treatment"]["standard-rated"]["sale"]["vat"] == "24750.00"
    context = next(e for e in audit_log.get_all() if e.action == "filing_guidance_context_retrieved")
    assert context.output["grounded"] is True
    assert audit_log.verify_chain().ok is True


def test_refundable_period_flags_the_position_and_still_submits(
    tmp_path, knowledge_base, audit_log, approval_queue
):
    folder = _one_file_folder(tmp_path, "refundable_period")
    run = _run(
        folder,
        narrative_client(narrative_payload(
            anomaly_codes=["net_refundable_position"], specialist_review=True
        )),
        knowledge_base, audit_log, approval_queue,
    )

    assert run.report.position == "refundable"
    assert [a.code for a in run.report.anomalies] == ["net_refundable_position"]
    assert run.report.narrative.specialist_review_needed is True
    assert run.approval_request.status == "pending"

    computed = next(e for e in audit_log.get_all() if e.action == "vat_provision_computed")
    assert computed.output["anomalies"][0]["code"] == "net_refundable_position"
    assert "refundable position" in computed.output["anomalies"][0]["detail"]
    assert audit_log.verify_chain().ok is True


def test_data_quality_issues_surface_every_anomaly_and_still_submits(
    tmp_path, knowledge_base, audit_log, approval_queue
):
    folder = _one_file_folder(tmp_path, "data_quality_issues")
    run = _run(
        folder,
        narrative_client(narrative_payload(
            anomaly_codes=["treatment_rate_mismatch", "unrecognized_treatment",
                           "unrecognized_transaction_type"],
            specialist_review=True,
        )),
        knowledge_base, audit_log, approval_queue,
    )

    codes = sorted(a.code for a in run.report.anomalies)
    assert codes == [
        "treatment_rate_mismatch", "treatment_rate_mismatch",
        "unrecognized_transaction_type", "unrecognized_treatment",
    ]
    summary = run.report.to_dict()["summary"]
    assert summary["transactions_excluded_from_totals"] == ["TXN-5003", "TXN-5004"]
    assert run.approval_request.status == "pending"
    assert audit_log.verify_chain().ok is True


def test_no_relevant_knowledge_still_drafts_an_ungrounded_narrative(
    tmp_path, empty_knowledge_base, audit_log, approval_queue
):
    folder = _one_file_folder(tmp_path, "normal_payable")
    run = _run(
        folder, narrative_client(narrative_payload(citation=None)),
        empty_knowledge_base, audit_log, approval_queue,
    )

    assert run.report.narrative_citations == []
    assert run.report.narrative_skipped_reason is None
    assert run.report.narrative.citations == []

    context = next(e for e in audit_log.get_all() if e.action == "filing_guidance_context_retrieved")
    assert context.output["grounded"] is False
    assert context.output["chunk_ids"] == []
    assert run.approval_request.status == "pending"
    assert audit_log.verify_chain().ok is True


def test_model_refusal_still_submits_the_provision_without_a_narrative(
    tmp_path, knowledge_base, audit_log, approval_queue
):
    folder = _one_file_folder(tmp_path, "normal_payable")
    run = _run(folder, refusal_client(category="cyber"), knowledge_base, audit_log, approval_queue)

    assert run.report.narrative is None
    assert run.report.narrative_skipped_reason == "narrative_refused"
    assert run.report.computed_transactions  # the deterministic calc is intact
    assert run.approval_request.status == "pending"

    refused = next(e for e in audit_log.get_all() if e.action == "filing_support_narrative_refused")
    assert refused.output["refusal_category"] == "cyber"
    assert refused.prompt_hash == run.report.narrative_prompt_hash

    actions = [e.action for e in audit_log.get_all()]
    assert "vat_provision_report_generated" in actions
    assert "approval_submitted:vat_provision" in actions
    assert audit_log.verify_chain().ok is True


def test_unparseable_narrative_still_submits_the_provision(
    tmp_path, knowledge_base, audit_log, approval_queue
):
    folder = _one_file_folder(tmp_path, "normal_payable")
    run = _run(folder, no_tool_call_client(), knowledge_base, audit_log, approval_queue)

    assert run.report.narrative is None
    assert run.report.narrative_skipped_reason == "narrative_failed"
    failed = next(e for e in audit_log.get_all() if e.action == "filing_support_narrative_failed")
    assert "no record_filing_support_narrative tool call" in failed.output["parse_error"]
    assert run.approval_request.status == "pending"
    assert audit_log.verify_chain().ok is True


def test_reviewer_can_reject_the_provision(tmp_path, knowledge_base, audit_log, approval_queue):
    folder = _one_file_folder(tmp_path, "refundable_period")
    run = _run(folder, narrative_client(narrative_payload()), knowledge_base, audit_log, approval_queue)

    rejected = approval_queue.decide(
        run.approval_request.id, actor="alice", role=Role.REVIEWER,
        decision=Decision.REJECT, timestamp=fixed_now(),
        comment="refund needs the tax lead to confirm the export evidence",
    )
    assert rejected.status == "rejected"
    assert audit_log.verify_chain().ok is True


def test_no_transactions_is_rejected(tmp_path, knowledge_base, audit_log, approval_queue):
    folder = tmp_path / "vat_transactions"
    folder.mkdir()
    with pytest.raises(ValueError, match="no VAT transactions"):
        _run(folder, ExplodingClient(), knowledge_base, audit_log, approval_queue)
    assert approval_queue.list_pending() == []


def test_mixed_currency_is_rejected(tmp_path, knowledge_base, audit_log, approval_queue):
    folder = tmp_path / "vat_transactions"
    folder.mkdir()
    (folder / "t.csv").write_text(
        "transaction_id,date,transaction_type,amount,vat_treatment,vat_rate,currency\n"
        "TXN-1,2026-07-01,sale,100.00,standard-rated,0.15,USD\n"
        "TXN-2,2026-07-02,sale,120.00,standard-rated,0.15,EUR\n"
    )
    with pytest.raises(ValueError, match="currenc"):
        _run(folder, ExplodingClient(), knowledge_base, audit_log, approval_queue)


def test_malformed_csv_raises_before_any_approval(tmp_path, knowledge_base, audit_log, approval_queue):
    folder = tmp_path / "vat_transactions"
    folder.mkdir()
    (folder / "t.csv").write_text(
        "transaction_id,date,transaction_type,amount,vat_treatment,vat_rate\n"
        "TXN-1,2026-07-01,sale,not-a-number,standard-rated,0.15\n"
    )
    from connectors import ConnectorParseError

    with pytest.raises(ConnectorParseError):
        _run(folder, ExplodingClient(), knowledge_base, audit_log, approval_queue)
    assert approval_queue.list_pending() == []
