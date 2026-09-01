"""Orchestrates one driver-based forecast: connector -> deterministic projection
-> narrative draft -> approvals.

Every step writes to the audit log, including the assumptions used and the
deterministic reason each line was flagged as high-sensitivity, so the run can
be reconstructed as auditor evidence (CLAUDE.md golden rule #3). The agent never
reads the actuals CSV itself — it goes through `platform/connectors` (rule #1) —
and never treats the forecast as a committed plan without a human approval
(rule #2): this function only ever produces a draft forecast for review.

`autonomy: draft_only`. Flagged lines do NOT stop submission — the forecast
still goes to a human, carrying the flags and the drafted narrative. A model
refusal or an unparseable narrative also does not stop submission: the
deterministic projection is complete on its own, so it goes through without a
narrative, with `narrative_skipped_reason` set.

Unlike agents/close-agent, this agent ALWAYS makes one Claude call — there is
always a trajectory and a set of assumptions to summarise, flagged or not.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Union

from approvals import ApprovalQueue, ApprovalRequest
from audit_log import AuditEvent, AuditLogStore
from connectors import BudgetActualLine, FileBudgetActualConnector
from knowledge import KnowledgeBase, SearchResult

from . import narrate
from .models import ForecastAssumptions, ForecastNarrative, ForecastReport
from .narrate import NarrativeResult, describe_flagged_line, describe_forecast
from .projection import project_forecast

AGENT_NAME = "fpa-agent"
KNOWLEDGE_CORPUS = "fpa_methodology"
DEFAULT_HORIZON = 3
DEFAULT_RETRIEVAL_TOP_K = 3


def _default_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _serialize_line(line: BudgetActualLine) -> dict:
    return {
        "source_capability": line.source_capability,
        "period": line.period,
        "account": line.account,
        "line_item": line.line_item,
        "category": line.category,
        "amount": str(line.amount),
        "currency": line.currency,
        "raw": line.raw,
    }


@dataclass
class ForecastRun:
    report: ForecastReport
    approval_request: ApprovalRequest
    narrative_result: NarrativeResult


def _retrieve_context(
    knowledge_base: KnowledgeBase, summary: dict, assumptions: dict,
    flagged_lines: list, top_k: int,
) -> list[SearchResult]:
    """One general forecast query plus one per flagged line, de-duplicated by
    chunk_id, order preserved (same shape as close_agent._retrieve_context)."""
    queries = [describe_forecast(summary, assumptions)]
    queries.extend(describe_flagged_line(fl) for fl in flagged_lines)

    seen: set[str] = set()
    chunks: list[SearchResult] = []
    for query in queries:
        for result in knowledge_base.search(query, top_k=top_k):
            if result.chunk.chunk_id not in seen:
                seen.add(result.chunk.chunk_id)
                chunks.append(result)
    return chunks


def run_driver_based_forecast(
    *,
    source_system: str,
    actuals_folder: Union[str, Path],
    knowledge_base: KnowledgeBase,
    audit_log: AuditLogStore,
    approval_queue: ApprovalQueue,
    assumptions: Optional[ForecastAssumptions] = None,
    horizon: int = DEFAULT_HORIZON,
    client=None,
    model: str = narrate.DEFAULT_MODEL,
    effort: str = narrate.DEFAULT_EFFORT,
    retrieval_top_k: int = DEFAULT_RETRIEVAL_TOP_K,
    preparer: str = AGENT_NAME,
    now: Callable[[], str] = _default_now,
) -> ForecastRun:
    if assumptions is None:
        assumptions = ForecastAssumptions()

    connector = FileBudgetActualConnector(
        source_system=source_system, actuals_folder=actuals_folder
    )
    actuals = [
        line for line in connector.fetch_lines() if line.source_capability == "actuals"
    ]

    if not actuals:
        raise ValueError(f"no historical actuals found in {actuals_folder!r}")

    currencies = sorted({line.currency for line in actuals})
    if len(currencies) > 1:
        raise ValueError(
            f"actuals mix currencies {currencies}; multi-currency forecasting is out of scope"
        )
    currency = currencies[0]

    historical_periods = sorted({line.period for line in actuals})

    audit_log.append(AuditEvent(
        timestamp=now(),
        agent=AGENT_NAME,
        action="historical_actuals_retrieved",
        actor=preparer,
        inputs={
            "source_system": source_system,
            "actuals_folder": str(actuals_folder),
            "horizon": horizon,
        },
        output={
            "line_count": len(actuals),
            "currency": currency,
            "historical_periods": historical_periods,
            "lines": [_serialize_line(line) for line in actuals],
        },
    ))

    # ---- deterministic projection (no LLM) --------------------------------
    projected_lines, base_period, projected_periods = project_forecast(
        actuals, assumptions=assumptions, horizon=horizon, currency=currency
    )
    flagged = [pl for pl in projected_lines if pl.flagged]

    report = ForecastReport(
        source_system=source_system,
        generated_at=now(),
        currency=currency,
        base_period=base_period,
        horizon=horizon,
        assumptions=assumptions.to_dict(),
        projected_periods=projected_periods,
        projected_lines=projected_lines,
        narrative=None,
        narrative_skipped_reason=None,
        model=None,
        narrative_prompt_hash=None,
        narrative_chunk_ids=[],
        narrative_citations=[],
    )
    summary = report.summary()

    audit_log.append(AuditEvent(
        timestamp=now(),
        agent=AGENT_NAME,
        action="forecast_projected",
        actor=preparer,
        inputs={"assumptions": assumptions.to_dict(), "horizon": horizon},
        output={
            "base_period": base_period,
            "projected_periods": projected_periods,
            "line_count": summary["line_count"],
            "flagged_count": len(flagged),
            "flagged": [
                {
                    "account": pl.account,
                    "line_item": pl.line_item,
                    "category": pl.category,
                    "period": pl.period,
                    "growth_rate": str(pl.growth_rate),
                    "growth_source": pl.growth_source,
                    "projected_amount": str(pl.projected_amount),
                    "flag_reasons": pl.flag_reasons,
                }
                for pl in flagged
            ],
        },
    ))

    # ---- narrative (always drafted) --------------------------------------
    flagged_dicts = summary["flagged_lines"]
    chunks = _retrieve_context(
        knowledge_base, summary, assumptions.to_dict(), flagged_dicts, retrieval_top_k
    )
    nar_chunk_ids = [r.chunk.chunk_id for r in chunks]
    nar_citations = [r.chunk.citation for r in chunks]

    audit_log.append(AuditEvent(
        timestamp=now(),
        agent=AGENT_NAME,
        action="narrative_context_retrieved",
        actor=preparer,
        inputs={"flagged_count": len(flagged), "top_k": retrieval_top_k},
        output={
            "grounded": bool(chunks),
            "chunk_ids": nar_chunk_ids,
            "citations": nar_citations,
        },
    ))

    if client is None:
        import anthropic
        client = anthropic.Anthropic()

    narrative_result = narrate.draft_forecast_narrative(
        client, summary, assumptions.to_dict(), flagged_dicts, chunks,
        model=model, effort=effort,
    )

    narrative: Optional[ForecastNarrative] = None
    skipped_reason: Optional[str] = None

    if narrative_result.narrative is None:
        if narrative_result.refused:
            skipped_reason = "narrative_refused"
            action = "forecast_narrative_refused"
            output = {"refusal_category": narrative_result.refusal_category}
        else:
            skipped_reason = "narrative_failed"
            action = "forecast_narrative_failed"
            output = {"parse_error": narrative_result.parse_error}
        audit_log.append(AuditEvent(
            timestamp=now(),
            agent=AGENT_NAME,
            action=action,
            actor=preparer,
            inputs={"chunk_ids": nar_chunk_ids},
            output=output,
            model=narrative_result.model,
            prompt_hash=narrative_result.prompt_hash,
        ))
    else:
        narrative = narrative_result.narrative
        audit_log.append(AuditEvent(
            timestamp=now(),
            agent=AGENT_NAME,
            action="forecast_narrative_drafted",
            actor=preparer,
            inputs={"chunk_ids": nar_chunk_ids},
            output={"citations": nar_citations, "narrative": narrative.to_dict()},
            model=narrative_result.model,
            prompt_hash=narrative_result.prompt_hash,
        ))

    report = ForecastReport(
        source_system=source_system,
        generated_at=report.generated_at,
        currency=currency,
        base_period=base_period,
        horizon=horizon,
        assumptions=assumptions.to_dict(),
        projected_periods=projected_periods,
        projected_lines=projected_lines,
        narrative=narrative,
        narrative_skipped_reason=skipped_reason,
        model=narrative_result.model,
        narrative_prompt_hash=narrative_result.prompt_hash,
        narrative_chunk_ids=nar_chunk_ids,
        narrative_citations=nar_citations,
    )
    report_dict = report.to_dict()

    audit_log.append(AuditEvent(
        timestamp=now(),
        agent=AGENT_NAME,
        action="forecast_report_generated",
        actor=preparer,
        inputs={},
        output=report_dict["summary"],
    ))

    approval_request = approval_queue.submit(
        agent=AGENT_NAME,
        action="forecast",
        payload=report_dict,
        preparer=preparer,
        timestamp=now(),
    )

    return ForecastRun(
        report=report,
        approval_request=approval_request,
        narrative_result=narrative_result,
    )
