from pathlib import Path

import pytest
from approvals import ApprovalQueue, Decision, Role
from audit_log import AuditLogStore
from knowledge import KnowledgeBase

from regulatory_change_agent import run_change_triage
from fakes import ExplodingClient, assessment_client, no_tool_call_client, refusal_client
from fixtures import ALL_DOCUMENTS, PROCEDURE_CITATION, SCENARIOS, assessment_payload

CONTROLS_DIR = Path(__file__).parent / "fixtures" / "controls"

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


def _run(client, knowledge_base, audit_log, approval_queue, *,
         requirement_text=None, requirement_reference=None, controls_folder=CONTROLS_DIR):
    return run_change_triage(
        source_system="larenthia_trading",
        requirement_text=requirement_text if requirement_text is not None else "placeholder",
        controls_folder=controls_folder,
        knowledge_base=knowledge_base,
        audit_log=audit_log,
        approval_queue=approval_queue,
        requirement_reference=requirement_reference,
        client=client,
        now=fixed_now,
    )


def _scenario(name, client, knowledge_base, audit_log, approval_queue):
    sc = SCENARIOS[name]
    return _run(
        client, knowledge_base, audit_log, approval_queue,
        requirement_text=sc["requirement_text"],
        requirement_reference=sc["requirement_reference"],
    )


def test_clear_match_flows_to_approved_with_a_grounded_assessment(
    knowledge_base, audit_log, approval_queue
):
    run = _scenario(
        "clear_match",
        assessment_client(assessment_payload(control_ids=["CTL-101"])),
        knowledge_base, audit_log, approval_queue,
    )

    report = run.report
    assert report.coverage_verdict == "apparent_coverage"
    assert report.gap_flagged is False
    assert [cr.control_id for cr in report.relevant_controls] == ["CTL-101"]
    assert report.narrative_skipped_reason is None
    assert report.narrative.gap_explanation is None
    assert report.narrative.review_required_statement
    assert report.narrative.citations == [PROCEDURE_CITATION]
    assert report.model == "claude-sonnet-5"

    request = run.approval_request
    assert request.status == "pending"
    assert request.preparer == "regulatory-change-agent"
    assert request.payload["summary"]["coverage_verdict"] == "apparent_coverage"
    assert request.payload["requirement_reference"] == "LT-REG-2026-014"

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
        "regulatory_change_received",
        "controls_retrieved",
        "impact_triaged",
        "triage_context_retrieved",
        "impact_assessment_drafted",
        "triage_report_generated",
        "approval_submitted:change_triage",
        "approval_reviewer_approve:change_triage",
        "approval_approver_approve:change_triage",
    ]

    received = next(e for e in audit_log.get_all() if e.action == "regulatory_change_received")
    assert received.output["requirement_reference"] == "LT-REG-2026-014"
    triaged = next(e for e in audit_log.get_all() if e.action == "impact_triaged")
    assert triaged.output["coverage_verdict"] == "apparent_coverage"
    assert triaged.output["surfaced_controls"][0]["control_id"] == "CTL-101"
    assert triaged.output["surfaced_controls"][0]["matched_terms"]
    context = next(e for e in audit_log.get_all() if e.action == "triage_context_retrieved")
    assert context.output["grounded"] is True
    drafted = next(e for e in audit_log.get_all() if e.action == "impact_assessment_drafted")
    assert drafted.prompt_hash == report.narrative_prompt_hash

    assert audit_log.verify_chain().ok is True


def test_genuine_gap_flags_a_likely_gap_and_still_submits(
    knowledge_base, audit_log, approval_queue
):
    run = _scenario(
        "genuine_gap",
        assessment_client(assessment_payload(control_ids=[], gap=True)),
        knowledge_base, audit_log, approval_queue,
    )

    assert run.report.coverage_verdict == "likely_gap"
    assert run.report.gap_flagged is True
    assert run.report.relevant_controls == []
    assert run.report.narrative.gap_explanation is not None
    assert run.approval_request.status == "pending"

    triaged = next(e for e in audit_log.get_all() if e.action == "impact_triaged")
    assert triaged.output["gap_flagged"] is True
    assert triaged.output["surfaced_controls"] == []
    assert any("no existing control" in r for r in triaged.output["flag_reasons"])
    assert audit_log.verify_chain().ok is True


def test_ambiguous_flags_weak_coverage_and_still_submits(
    knowledge_base, audit_log, approval_queue
):
    run = _scenario(
        "ambiguous",
        assessment_client(assessment_payload(control_ids=["CTL-104", "CTL-103"], gap=True)),
        knowledge_base, audit_log, approval_queue,
    )

    assert run.report.coverage_verdict == "weak_coverage"
    assert run.report.gap_flagged is True
    assert [cr.control_id for cr in run.report.relevant_controls] == ["CTL-104", "CTL-103"]
    assert any("strongest apparent match is CTL-104" in r for r in run.report.flag_reasons)
    assert run.approval_request.status == "pending"
    assert audit_log.verify_chain().ok is True


def test_no_relevant_knowledge_still_drafts_an_ungrounded_assessment(
    empty_knowledge_base, audit_log, approval_queue
):
    run = _scenario(
        "clear_match",
        assessment_client(assessment_payload(control_ids=["CTL-101"], citation=None)),
        empty_knowledge_base, audit_log, approval_queue,
    )

    assert run.report.narrative_citations == []
    assert run.report.narrative_skipped_reason is None
    assert run.report.narrative.citations == []

    context = next(e for e in audit_log.get_all() if e.action == "triage_context_retrieved")
    assert context.output["grounded"] is False
    assert context.output["chunk_ids"] == []
    assert run.approval_request.status == "pending"
    assert audit_log.verify_chain().ok is True


def test_model_refusal_still_submits_the_triage_without_a_narrative(
    knowledge_base, audit_log, approval_queue
):
    run = _scenario("clear_match", refusal_client(category="cyber"), knowledge_base, audit_log, approval_queue)

    assert run.report.narrative is None
    assert run.report.narrative_skipped_reason == "narrative_refused"
    assert run.report.relevances  # the deterministic triage is intact
    assert run.approval_request.status == "pending"

    refused = next(e for e in audit_log.get_all() if e.action == "impact_assessment_refused")
    assert refused.output["refusal_category"] == "cyber"
    assert refused.prompt_hash == run.report.narrative_prompt_hash

    actions = [e.action for e in audit_log.get_all()]
    assert "triage_report_generated" in actions
    assert "approval_submitted:change_triage" in actions
    assert audit_log.verify_chain().ok is True


def test_unparseable_narrative_still_submits_the_triage(
    knowledge_base, audit_log, approval_queue
):
    run = _scenario("clear_match", no_tool_call_client(), knowledge_base, audit_log, approval_queue)

    assert run.report.narrative is None
    assert run.report.narrative_skipped_reason == "narrative_failed"
    failed = next(e for e in audit_log.get_all() if e.action == "impact_assessment_failed")
    assert "no record_impact_assessment tool call" in failed.output["parse_error"]
    assert run.approval_request.status == "pending"
    assert audit_log.verify_chain().ok is True


def test_reviewer_can_reject_the_triage(knowledge_base, audit_log, approval_queue):
    run = _scenario(
        "genuine_gap",
        assessment_client(assessment_payload(control_ids=[], gap=True)),
        knowledge_base, audit_log, approval_queue,
    )

    rejected = approval_queue.decide(
        run.approval_request.id, actor="alice", role=Role.REVIEWER,
        decision=Decision.REJECT, timestamp=fixed_now(),
        comment="compliance to assess the suspected gap and confirm scope",
    )
    assert rejected.status == "rejected"
    assert audit_log.verify_chain().ok is True


def test_blank_requirement_is_rejected(knowledge_base, audit_log, approval_queue):
    with pytest.raises(ValueError, match="requirement_text is empty"):
        _run(ExplodingClient(), knowledge_base, audit_log, approval_queue, requirement_text="   ")
    assert approval_queue.list_pending() == []


def test_no_controls_is_rejected(tmp_path, knowledge_base, audit_log, approval_queue):
    empty = tmp_path / "controls"
    empty.mkdir()
    with pytest.raises(ValueError, match="no internal controls"):
        _run(
            ExplodingClient(), knowledge_base, audit_log, approval_queue,
            requirement_text="A new requirement", controls_folder=empty,
        )
    # the change was logged before the controls fetch failed, but nothing was submitted
    assert approval_queue.list_pending() == []
