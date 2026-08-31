from decimal import Decimal
from pathlib import Path

import pytest
from approvals import ApprovalQueue, Decision, Role
from audit_log import AuditLogStore
from knowledge import KnowledgeBase

from close_agent import FlagThresholds, run_close_variance_analysis
from fakes import ExplodingClient, explanations_client, refusal_client
from fixtures import ALL_DOCUMENTS, POLICY_CITATION, explanations_payload

FIX = Path(__file__).parent / "fixtures"
BUDGET = FIX / "budget"
ACTUALS = FIX / "actuals"

TS = iter(f"2026-09-30T{i // 60:02d}:{i % 60:02d}:00Z" for i in range(180))


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


def _run(period, client, knowledge_base, audit_log, approval_queue, thresholds=None):
    return run_close_variance_analysis(
        source_system="larenthia_trading",
        period=period,
        budget_folder=BUDGET,
        actuals_folder=ACTUALS,
        knowledge_base=knowledge_base,
        audit_log=audit_log,
        approval_queue=approval_queue,
        client=client,
        thresholds=thresholds,
        now=fixed_now,
    )


EVENTFUL_FLAGGED = [
    ("5000", "Cost of goods sold"),
    ("6000", "Marketing — paid media"),
    ("6200", "Contract labour"),
    ("6900", "Regulatory consulting"),
]


def test_eventful_period_flows_to_approved_with_cited_explanations(
    knowledge_base, audit_log, approval_queue
):
    run = _run(
        "2026-07",
        explanations_client(explanations_payload(EVENTFUL_FLAGGED)),
        knowledge_base, audit_log, approval_queue,
        thresholds=FlagThresholds(pct=Decimal("0.10"), amount=Decimal("25000")),
    )

    assert len(run.report.line_variances) == 6
    assert [lv.account for lv in run.report.flagged] == ["5000", "6000", "6200", "6900"]

    cogs = next(lv for lv in run.report.flagged if lv.account == "5000")
    assert cogs.flag_reasons == ["absolute variance $40,000.00 >= threshold $25,000.00"]

    unbudgeted = next(lv for lv in run.report.flagged if lv.account == "6900")
    assert unbudgeted.presence == "actual_only"
    assert unbudgeted.pct_variance is None

    assert run.report.explanations_skipped_reason is None
    assert [e.account for e in run.report.explanations] == ["5000", "6000", "6200", "6900"]
    assert all(e.citations == [POLICY_CITATION] for e in run.report.explanations)

    summary = run.report.to_dict()["summary"]
    assert summary["flagged_count"] == 4
    assert summary["total_variance"] == "52150.00"
    assert summary["largest_over_budget"]["account"] == "5000"
    assert summary["largest_under_budget"]["account"] == "6200"

    request = run.approval_request
    assert request.status == "pending"
    assert request.current_stage == Role.REVIEWER.value
    assert request.preparer == "close-agent"
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
        "budget_actuals_retrieved",
        "variances_computed",
        "explanation_context_retrieved",
        "variance_explanations_drafted",
        "variance_report_generated",
        "approval_submitted:variance_report",
        "approval_reviewer_approve:variance_report",
        "approval_approver_approve:variance_report",
    ]

    events = audit_log.get_all()
    computed = events[1]
    assert computed.output["flagged_count"] == 4
    flagged_accounts = [f["account"] for f in computed.output["flagged"]]
    assert flagged_accounts == ["5000", "6000", "6200", "6900"]
    assert any("zero budget" in r for r in computed.output["flagged"][3]["flag_reasons"])

    drafted = events[3]
    assert drafted.model == "claude-sonnet-5"
    assert drafted.prompt_hash == run.report.explanation_prompt_hash
    # event "citations" is the context offered to the model (every retrieved chunk);
    # each explanation carries the subset the model actually cited.
    assert POLICY_CITATION in drafted.output["citations"]
    assert [e["citations"] for e in drafted.output["explanations"]] == [[POLICY_CITATION]] * 4

    assert audit_log.verify_chain().ok is True


def test_quiet_period_submits_without_calling_the_model(
    knowledge_base, audit_log, approval_queue
):
    run = _run("2026-08", ExplodingClient(), knowledge_base, audit_log, approval_queue)

    assert run.report.flagged == []
    assert run.report.explanations == []
    assert run.report.explanations_skipped_reason == "no_flagged_variances"
    assert run.explanation_result is None
    assert run.approval_request.status == "pending"

    actions = [e.action for e in audit_log.get_all()]
    assert actions == [
        "budget_actuals_retrieved",
        "variances_computed",
        "variance_explanations_skipped",
        "variance_report_generated",
        "approval_submitted:variance_report",
    ]
    skipped = next(e for e in audit_log.get_all() if e.action == "variance_explanations_skipped")
    assert skipped.output["reason"] == "no_flagged_variances"
    assert audit_log.verify_chain().ok is True


def test_no_relevant_knowledge_still_drafts_ungrounded_explanations(
    empty_knowledge_base, audit_log, approval_queue
):
    default_flagged = [
        ("6000", "Marketing — paid media"),
        ("6200", "Contract labour"),
        ("6900", "Regulatory consulting"),
    ]
    run = _run(
        "2026-07",
        explanations_client(explanations_payload(default_flagged, citation=None)),
        empty_knowledge_base, audit_log, approval_queue,
    )

    assert [lv.account for lv in run.report.flagged] == ["6000", "6200", "6900"]
    assert run.report.explanation_citations == []
    assert run.report.explanations_skipped_reason is None
    assert all(e.citations == [] for e in run.report.explanations)

    context = next(
        e for e in audit_log.get_all() if e.action == "explanation_context_retrieved"
    )
    assert context.output["grounded"] is False
    assert context.output["chunk_ids"] == []
    assert audit_log.verify_chain().ok is True


def test_model_refusal_still_submits_report_without_explanations(
    knowledge_base, audit_log, approval_queue
):
    run = _run("2026-07", refusal_client(category="cyber"), knowledge_base, audit_log, approval_queue)

    assert run.report.explanations == []
    assert run.report.explanations_skipped_reason == "explanation_refused"
    assert run.approval_request.status == "pending"

    refused = next(
        e for e in audit_log.get_all() if e.action == "variance_explanations_refused"
    )
    assert refused.output["refusal_category"] == "cyber"
    assert refused.model == "claude-sonnet-5"
    assert refused.prompt_hash == run.report.explanation_prompt_hash

    actions = [e.action for e in audit_log.get_all()]
    assert "variance_report_generated" in actions
    assert "approval_submitted:variance_report" in actions
    assert audit_log.verify_chain().ok is True


def test_reviewer_can_reject_the_report(knowledge_base, audit_log, approval_queue):
    run = _run(
        "2026-07",
        explanations_client(explanations_payload(EVENTFUL_FLAGGED)),
        knowledge_base, audit_log, approval_queue,
        thresholds=FlagThresholds(pct=Decimal("0.10"), amount=Decimal("25000")),
    )

    rejected = approval_queue.decide(
        run.approval_request.id, actor="alice", role=Role.REVIEWER,
        decision=Decision.REJECT, timestamp=fixed_now(), comment="need more detail on COGS",
    )
    assert rejected.status == "rejected"
    assert audit_log.verify_chain().ok is True


def test_presence_rules_surface_missing_actual_and_unbudgeted_end_to_end(
    knowledge_base, audit_log, approval_queue
):
    flagged = [
        ("4000", "Product revenue"),
        ("6300", "Software subscriptions"),
        ("6500", "Bank fees"),
    ]
    run = _run(
        "2026-09",
        explanations_client(explanations_payload(flagged)),
        knowledge_base, audit_log, approval_queue,
    )

    assert [lv.account for lv in run.report.flagged] == ["4000", "6300", "6500"]

    subs = next(lv for lv in run.report.flagged if lv.account == "6300")
    assert subs.presence == "budget_only"
    assert any("no actual reported" in r for r in subs.flag_reasons)

    fees = next(lv for lv in run.report.flagged if lv.account == "6500")
    assert fees.presence == "actual_only"

    assert len(run.report.explanations) == 3
    assert audit_log.verify_chain().ok is True


def test_mixed_currency_is_rejected(tmp_path, knowledge_base, audit_log, approval_queue):
    b = tmp_path / "budget"
    b.mkdir()
    (b / "p.csv").write_text(
        "period,account,line_item,category,amount,currency\n"
        "2026-07,6000,Marketing,Operating expenses,100.00,USD\n"
    )
    a = tmp_path / "actuals"
    a.mkdir()
    (a / "p.csv").write_text(
        "period,account,line_item,category,amount,currency\n"
        "2026-07,6000,Marketing,Operating expenses,120.00,EUR\n"
    )

    with pytest.raises(ValueError, match="currenc"):
        run_close_variance_analysis(
            source_system="larenthia_trading", period="2026-07",
            budget_folder=b, actuals_folder=a, knowledge_base=knowledge_base,
            audit_log=audit_log, approval_queue=approval_queue,
            client=explanations_client(explanations_payload([])), now=fixed_now,
        )


def test_unknown_period_is_rejected(knowledge_base, audit_log, approval_queue):
    with pytest.raises(ValueError, match="no budget or actuals"):
        _run("2099-01", ExplodingClient(), knowledge_base, audit_log, approval_queue)
