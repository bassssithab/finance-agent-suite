from decimal import Decimal
from pathlib import Path

import pytest
from approvals import ApprovalQueue, Decision, Role
from audit_log import AuditLogStore
from knowledge import KnowledgeBase

from controls_sox_agent import ControlPolicy, run_journal_entry_control_test
from fakes import ExplodingClient, narratives_client, no_tool_call_client, refusal_client
from fixtures import ALL_DOCUMENTS, POLICY_CITATION, narratives_payload

JE_DIR = Path(__file__).parent / "fixtures" / "journal_entries"

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
    folder = tmp_path / "journal_entries"
    folder.mkdir(exist_ok=True)
    (folder / f"{name}.csv").write_text((JE_DIR / f"{name}.csv").read_text())
    return folder


def _run(folder, client, knowledge_base, audit_log, approval_queue, policy=None):
    return run_journal_entry_control_test(
        source_system="larenthia_trading",
        entries_folder=folder,
        knowledge_base=knowledge_base,
        audit_log=audit_log,
        approval_queue=approval_queue,
        client=client,
        policy=policy or ControlPolicy(dual_approval_threshold=Decimal("50000")),
        now=fixed_now,
    )


SOD_FLAGGED = [
    ("JE-3001", "preparer_is_approver"),
    ("JE-3002", "missing_second_approver"),
    ("JE-3003", "duplicate_approvers"),
    ("JE-3004", "preparer_is_approver"),
]


def test_clean_batch_submits_without_calling_the_model(
    tmp_path, knowledge_base, audit_log, approval_queue
):
    folder = _one_file_folder(tmp_path, "clean_batch")
    run = _run(folder, ExplodingClient(), knowledge_base, audit_log, approval_queue)

    assert [r.passed for r in run.report.results] == [True] * 5
    assert run.report.violations == []
    assert run.report.narratives == []
    assert run.report.narratives_skipped_reason == "no_violations"
    assert run.narrative_result is None
    assert run.approval_request.status == "pending"
    assert run.approval_request.current_stage == Role.REVIEWER.value

    actions = [e.action for e in audit_log.get_all()]
    assert actions == [
        "journal_entries_retrieved",
        "sod_control_tested",
        "deficiency_narratives_skipped",
        "control_test_report_generated",
        "approval_submitted:control_test_report",
    ]

    tested = next(e for e in audit_log.get_all() if e.action == "sod_control_tested")
    assert tested.output["entries_tested"] == [
        "JE-2001", "JE-2002", "JE-2003", "JE-2004", "JE-2005"
    ]
    assert tested.output["violation_count"] == 0
    assert audit_log.verify_chain().ok is True


def test_sod_violations_flow_to_approved_with_cited_narratives(
    tmp_path, knowledge_base, audit_log, approval_queue
):
    folder = _one_file_folder(tmp_path, "sod_violations")
    run = _run(
        folder,
        narratives_client(narratives_payload(SOD_FLAGGED)),
        knowledge_base, audit_log, approval_queue,
    )

    report = run.report
    assert report.summary()["entries_tested"] == 6
    assert report.summary()["entries_with_violations"] == 4
    assert report.summary()["violation_count"] == 4
    assert report.summary()["violations_by_code"] == {
        "preparer_is_approver": 2,
        "missing_second_approver": 1,
        "duplicate_approvers": 1,
    }

    assert [(n.entry_id, n.violation_code) for n in report.narratives] == SOD_FLAGGED
    assert all(n.citations == [POLICY_CITATION] for n in report.narratives)
    assert report.narratives_skipped_reason is None
    assert report.model == "claude-sonnet-5"

    request = run.approval_request
    assert request.preparer == "controls-sox-agent"
    assert request.payload["summary"]["violation_count"] == 4
    assert request.payload["control_id"] == "JE-SOD-001"

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
        "journal_entries_retrieved",
        "sod_control_tested",
        "deficiency_context_retrieved",
        "deficiency_narratives_drafted",
        "control_test_report_generated",
        "approval_submitted:control_test_report",
        "approval_reviewer_approve:control_test_report",
        "approval_approver_approve:control_test_report",
    ]

    tested = next(e for e in audit_log.get_all() if e.action == "sod_control_tested")
    assert tested.output["entries_tested"] == [
        "JE-3001", "JE-3002", "JE-3003", "JE-3004", "JE-3005", "JE-3006"
    ]
    flagged_in_audit = [(v["entry_id"], v["code"]) for v in tested.output["violations"]]
    assert flagged_in_audit == SOD_FLAGGED
    # every flagged row carries its deterministic reason
    assert all(v["detail"] for v in tested.output["violations"])
    je3002 = next(v for v in tested.output["violations"] if v["entry_id"] == "JE-3002")
    assert "dual-approval threshold" in je3002["detail"]
    assert je3002["dual_approval_required"] is True

    drafted = next(e for e in audit_log.get_all() if e.action == "deficiency_narratives_drafted")
    assert drafted.prompt_hash == report.narrative_prompt_hash
    assert POLICY_CITATION in drafted.output["citations"]

    assert audit_log.verify_chain().ok is True


def test_edge_cases_file_covers_boundary_no_approver_and_name_normalization(
    tmp_path, knowledge_base, audit_log, approval_queue
):
    folder = _one_file_folder(tmp_path, "edge_cases")
    flagged = [
        ("JE-4001", "missing_second_approver"),
        ("JE-4002", "no_approver"),
        ("JE-4003", "duplicate_approvers"),
    ]
    run = _run(
        folder,
        narratives_client(narratives_payload(flagged)),
        knowledge_base, audit_log, approval_queue,
    )

    by_id = {r.entry_id: r for r in run.report.results}
    # exactly at the $50,000 threshold -> dual approval required
    assert by_id["JE-4001"].dual_approval_required is True
    assert [v.code for v in by_id["JE-4001"].violations] == ["missing_second_approver"]
    # "R.Singh " and "r.singh" are the same person after strip + casefold
    assert [v.code for v in by_id["JE-4003"].violations] == ["duplicate_approvers"]
    # a below-threshold single-approver entry is clean
    assert by_id["JE-4004"].passed is True

    assert [(n.entry_id, n.violation_code) for n in run.report.narratives] == flagged
    assert audit_log.verify_chain().ok is True


def test_no_relevant_knowledge_still_drafts_ungrounded_narratives(
    tmp_path, empty_knowledge_base, audit_log, approval_queue
):
    folder = _one_file_folder(tmp_path, "sod_violations")
    run = _run(
        folder,
        narratives_client(narratives_payload(SOD_FLAGGED, citation=None)),
        empty_knowledge_base, audit_log, approval_queue,
    )

    assert run.report.narrative_citations == []
    assert run.report.narratives_skipped_reason is None
    assert all(n.citations == [] for n in run.report.narratives)

    context = next(
        e for e in audit_log.get_all() if e.action == "deficiency_context_retrieved"
    )
    assert context.output["grounded"] is False
    assert context.output["chunk_ids"] == []
    assert audit_log.verify_chain().ok is True


def test_model_refusal_still_submits_report_without_narratives(
    tmp_path, knowledge_base, audit_log, approval_queue
):
    folder = _one_file_folder(tmp_path, "sod_violations")
    run = _run(folder, refusal_client(category="cyber"), knowledge_base, audit_log, approval_queue)

    assert run.report.narratives == []
    assert run.report.narratives_skipped_reason == "deficiency_narratives_refused"
    assert run.approval_request.status == "pending"

    refused = next(
        e for e in audit_log.get_all() if e.action == "deficiency_narratives_refused"
    )
    assert refused.output["refusal_category"] == "cyber"
    assert refused.prompt_hash == run.report.narrative_prompt_hash

    actions = [e.action for e in audit_log.get_all()]
    assert "control_test_report_generated" in actions
    assert "approval_submitted:control_test_report" in actions
    assert audit_log.verify_chain().ok is True


def test_unparseable_model_response_still_submits_report(
    tmp_path, knowledge_base, audit_log, approval_queue
):
    folder = _one_file_folder(tmp_path, "sod_violations")
    run = _run(folder, no_tool_call_client(), knowledge_base, audit_log, approval_queue)

    assert run.report.narratives == []
    assert run.report.narratives_skipped_reason == "deficiency_narratives_failed"
    failed = next(
        e for e in audit_log.get_all() if e.action == "deficiency_narratives_failed"
    )
    assert "no record_deficiency_narratives tool call" in failed.output["parse_error"]
    assert run.approval_request.status == "pending"
    assert audit_log.verify_chain().ok is True


def test_reviewer_can_reject_the_report(tmp_path, knowledge_base, audit_log, approval_queue):
    folder = _one_file_folder(tmp_path, "sod_violations")
    run = _run(
        folder,
        narratives_client(narratives_payload(SOD_FLAGGED)),
        knowledge_base, audit_log, approval_queue,
    )

    rejected = approval_queue.decide(
        run.approval_request.id, actor="alice", role=Role.REVIEWER,
        decision=Decision.REJECT, timestamp=fixed_now(),
        comment="JE-3002 approver is on leave — re-check",
    )
    assert rejected.status == "rejected"
    assert audit_log.verify_chain().ok is True


def test_empty_folder_is_rejected(tmp_path, knowledge_base, audit_log, approval_queue):
    folder = tmp_path / "journal_entries"
    folder.mkdir()
    with pytest.raises(ValueError, match="no journal entries"):
        _run(folder, ExplodingClient(), knowledge_base, audit_log, approval_queue)


def test_mixed_currency_is_rejected(tmp_path, knowledge_base, audit_log, approval_queue):
    folder = tmp_path / "journal_entries"
    folder.mkdir()
    (folder / "je.csv").write_text(
        "entry_id,date,account,amount,preparer,approver_1,approver_2,currency\n"
        "JE-1,2026-07-01,6000,100.00,alice,bob,,USD\n"
        "JE-2,2026-07-02,6000,120.00,alice,bob,,EUR\n"
    )
    with pytest.raises(ValueError, match="currenc"):
        _run(folder, ExplodingClient(), knowledge_base, audit_log, approval_queue)


def test_malformed_csv_raises_before_any_approval(tmp_path, knowledge_base, audit_log, approval_queue):
    folder = tmp_path / "journal_entries"
    folder.mkdir()
    (folder / "je.csv").write_text(
        "entry_id,date,account,amount,preparer,approver_1,approver_2\n"
        "JE-1,2026-07-01,6000,not-a-number,alice,bob,\n"
    )
    from connectors import ConnectorParseError

    with pytest.raises(ConnectorParseError):
        _run(folder, ExplodingClient(), knowledge_base, audit_log, approval_queue)
    assert approval_queue.list_pending() == []
