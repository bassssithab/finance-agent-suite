from decimal import Decimal
from pathlib import Path

import pytest
from approvals import ApprovalQueue, Decision, Role
from audit_log import AuditLogStore
from knowledge import KnowledgeBase

from fpa_agent import ForecastAssumptions, run_driver_based_forecast
from fakes import ExplodingClient, narrative_client, no_tool_call_client, refusal_client
from fixtures import ALL_DOCUMENTS, METHODOLOGY_CITATION, narrative_payload

ACTUALS_DIR = Path(__file__).parent / "fixtures" / "actuals"

TS = iter(f"2026-06-30T{i // 60:02d}:{i % 60:02d}:00Z" for i in range(240))


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


def _run(client, knowledge_base, audit_log, approval_queue, *, assumptions=None, horizon=3, folder=ACTUALS_DIR):
    return run_driver_based_forecast(
        source_system="larenthia_trading",
        actuals_folder=folder,
        knowledge_base=knowledge_base,
        audit_log=audit_log,
        approval_queue=approval_queue,
        assumptions=assumptions,
        horizon=horizon,
        client=client,
        now=fixed_now,
    )


def _distinct_flagged(report):
    return sorted({pl.line_item for pl in report.flagged})


def test_steady_forecast_flows_to_approved_with_a_grounded_narrative(
    knowledge_base, audit_log, approval_queue
):
    run = _run(
        narrative_client(narrative_payload()),
        knowledge_base, audit_log, approval_queue,
    )

    report = run.report
    assert report.base_period == "2026-06"
    assert report.projected_periods == ["2026-07", "2026-08", "2026-09"]
    # only 6300 flags, and only for stale_base
    assert _distinct_flagged(report) == ["Software subscriptions"]
    assert all(
        r.startswith("no actual for base period")
        for pl in report.flagged for r in pl.flag_reasons
    )

    assert report.narrative_skipped_reason is None
    assert report.narrative.citations == [METHODOLOGY_CITATION]
    assert report.model == "claude-sonnet-5"

    request = run.approval_request
    assert request.status == "pending"
    assert request.preparer == "fpa-agent"
    assert request.payload["summary"]["base_period"] == "2026-06"

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
        "historical_actuals_retrieved",
        "forecast_projected",
        "narrative_context_retrieved",
        "forecast_narrative_drafted",
        "forecast_report_generated",
        "approval_submitted:forecast",
        "approval_reviewer_approve:forecast",
        "approval_approver_approve:forecast",
    ]

    projected = next(e for e in audit_log.get_all() if e.action == "forecast_projected")
    assert projected.output["base_period"] == "2026-06"
    assert projected.output["flagged_count"] == 3  # 6300 x 3 periods
    context = next(e for e in audit_log.get_all() if e.action == "narrative_context_retrieved")
    assert context.output["grounded"] is True
    drafted = next(e for e in audit_log.get_all() if e.action == "forecast_narrative_drafted")
    assert drafted.model == "claude-sonnet-5"
    assert drafted.prompt_hash == report.narrative_prompt_hash

    assert audit_log.verify_chain().ok is True


def test_aggressive_revenue_growth_flags_high_sensitivity_lines(
    knowledge_base, audit_log, approval_queue
):
    run = _run(
        narrative_client(narrative_payload(
            flagged_line_items=["4000 Product revenue", "4100 Services revenue"]
        )),
        knowledge_base, audit_log, approval_queue,
        assumptions=ForecastAssumptions(category_growth={"Revenue": Decimal("0.30")}),
    )

    report = run.report
    assert _distinct_flagged(report) == [
        "Product revenue", "Services revenue", "Software subscriptions"
    ]
    revenue = next(pl for pl in report.flagged if pl.line_item == "Product revenue")
    assert revenue.growth_rate == Decimal("0.30")
    assert revenue.growth_source == "category"
    assert any("sensitivity threshold" in r for r in revenue.flag_reasons)

    assert report.narrative.flagged_items_called_out  # model was asked to call them out

    projected = next(e for e in audit_log.get_all() if e.action == "forecast_projected")
    flagged_items = {f["line_item"] for f in projected.output["flagged"]}
    assert {"Product revenue", "Services revenue"} <= flagged_items
    assert all(f["flag_reasons"] for f in projected.output["flagged"])
    assert run.approval_request.status == "pending"
    assert audit_log.verify_chain().ok is True


def test_negative_growth_flags_lines_that_project_below_zero(
    knowledge_base, audit_log, approval_queue
):
    run = _run(
        narrative_client(narrative_payload(flagged_line_items=["several lines"])),
        knowledge_base, audit_log, approval_queue,
        assumptions=ForecastAssumptions(default_growth=Decimal("-1.5")),
        horizon=2,
    )

    negative_reasons = [
        r for pl in run.report.flagged for r in pl.flag_reasons if "below zero" in r
    ]
    assert negative_reasons
    assert run.approval_request.status == "pending"
    assert audit_log.verify_chain().ok is True


def test_no_relevant_knowledge_still_drafts_an_ungrounded_narrative(
    empty_knowledge_base, audit_log, approval_queue
):
    run = _run(
        narrative_client(narrative_payload(citation=None)),
        empty_knowledge_base, audit_log, approval_queue,
    )

    assert run.report.narrative_citations == []
    assert run.report.narrative_skipped_reason is None
    assert run.report.narrative.citations == []

    context = next(e for e in audit_log.get_all() if e.action == "narrative_context_retrieved")
    assert context.output["grounded"] is False
    assert context.output["chunk_ids"] == []
    assert run.approval_request.status == "pending"
    assert audit_log.verify_chain().ok is True


def test_model_refusal_still_submits_the_forecast_without_a_narrative(
    knowledge_base, audit_log, approval_queue
):
    run = _run(refusal_client(category="cyber"), knowledge_base, audit_log, approval_queue)

    assert run.report.narrative is None
    assert run.report.narrative_skipped_reason == "narrative_refused"
    assert run.report.projected_lines  # the deterministic projection is intact
    assert run.approval_request.status == "pending"

    refused = next(e for e in audit_log.get_all() if e.action == "forecast_narrative_refused")
    assert refused.output["refusal_category"] == "cyber"
    assert refused.prompt_hash == run.report.narrative_prompt_hash

    actions = [e.action for e in audit_log.get_all()]
    assert "forecast_report_generated" in actions
    assert "approval_submitted:forecast" in actions
    assert audit_log.verify_chain().ok is True


def test_unparseable_narrative_still_submits_the_forecast(
    knowledge_base, audit_log, approval_queue
):
    run = _run(no_tool_call_client(), knowledge_base, audit_log, approval_queue)

    assert run.report.narrative is None
    assert run.report.narrative_skipped_reason == "narrative_failed"
    failed = next(e for e in audit_log.get_all() if e.action == "forecast_narrative_failed")
    assert "no record_forecast_narrative tool call" in failed.output["parse_error"]
    assert run.approval_request.status == "pending"
    assert audit_log.verify_chain().ok is True


def test_reviewer_can_reject_the_forecast(knowledge_base, audit_log, approval_queue):
    run = _run(narrative_client(narrative_payload()), knowledge_base, audit_log, approval_queue)

    rejected = approval_queue.decide(
        run.approval_request.id, actor="alice", role=Role.REVIEWER,
        decision=Decision.REJECT, timestamp=fixed_now(),
        comment="Revenue growth assumption needs pipeline support",
    )
    assert rejected.status == "rejected"
    assert audit_log.verify_chain().ok is True


def test_no_actuals_is_rejected(tmp_path, knowledge_base, audit_log, approval_queue):
    empty = tmp_path / "actuals"
    empty.mkdir()
    with pytest.raises(ValueError, match="no historical actuals"):
        _run(ExplodingClient(), knowledge_base, audit_log, approval_queue, folder=empty)
    assert approval_queue.list_pending() == []


def test_mixed_currency_is_rejected(tmp_path, knowledge_base, audit_log, approval_queue):
    folder = tmp_path / "actuals"
    folder.mkdir()
    (folder / "p.csv").write_text(
        "period,account,line_item,category,amount,currency\n"
        "2026-06,4000,Product revenue,Revenue,100.00,USD\n"
        "2026-06,4100,Services revenue,Revenue,120.00,EUR\n"
    )
    with pytest.raises(ValueError, match="currenc"):
        _run(ExplodingClient(), knowledge_base, audit_log, approval_queue, folder=folder)


def test_non_monthly_period_is_rejected(tmp_path, knowledge_base, audit_log, approval_queue):
    folder = tmp_path / "actuals"
    folder.mkdir()
    (folder / "p.csv").write_text(
        "period,account,line_item,category,amount,currency\n"
        "2026-Q2,4000,Product revenue,Revenue,100.00,USD\n"
    )
    with pytest.raises(ValueError, match="YYYY-MM"):
        _run(ExplodingClient(), knowledge_base, audit_log, approval_queue, folder=folder)
