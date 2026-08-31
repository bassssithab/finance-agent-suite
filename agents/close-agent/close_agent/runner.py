"""Orchestrates one close variance analysis: connector -> deterministic
variances -> optional cited explanations -> approvals.

Every step writes to the audit log, including which variances were flagged and
the deterministic reason each was flagged, so the run can be reconstructed as
auditor evidence (CLAUDE.md golden rule #3). The agent never reads the
budget/actuals CSVs itself — it goes through `platform/connectors` (rule #1) —
and never treats the report as final without a human approval (rule #2): this
function only ever produces a draft report for review.

`autonomy: draft_only`. Flagged variances do NOT stop submission — the report
still goes to a human, carrying the flags and the drafted explanations,
because the agent does not get to decide (rule #2). A model refusal or an
unparseable response also does not stop submission: the report goes through
without explanations, with `explanations_skipped_reason` set.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Union

from approvals import ApprovalQueue, ApprovalRequest
from audit_log import AuditEvent, AuditLogStore
from connectors import BudgetActualLine, FileBudgetActualConnector
from knowledge import KnowledgeBase, SearchResult

from . import explain
from .explain import ExplanationResult, describe_variance
from .models import FlagThresholds, VarianceExplanation, VarianceReport
from .variance import compute_variances

AGENT_NAME = "close-agent"
KNOWLEDGE_CORPUS = "accounting_policy"
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
class CloseVarianceRun:
    report: VarianceReport
    approval_request: ApprovalRequest
    explanation_result: Optional[ExplanationResult]  # None when nothing was flagged


def _retrieve_context(
    knowledge_base: KnowledgeBase, flagged, top_k: int
) -> list[SearchResult]:
    """One search per flagged line, de-duplicated by chunk_id, order preserved
    (same shape as ap_agent._retrieve_coding_context)."""
    seen: set[str] = set()
    chunks: list[SearchResult] = []
    for lv in flagged:
        for result in knowledge_base.search(describe_variance(lv), top_k=top_k):
            if result.chunk.chunk_id not in seen:
                seen.add(result.chunk.chunk_id)
                chunks.append(result)
    return chunks


def run_close_variance_analysis(
    *,
    source_system: str,
    period: str,
    budget_folder: Union[str, Path],
    actuals_folder: Union[str, Path],
    knowledge_base: KnowledgeBase,
    audit_log: AuditLogStore,
    approval_queue: ApprovalQueue,
    client=None,
    model: str = explain.DEFAULT_MODEL,
    effort: str = explain.DEFAULT_EFFORT,
    thresholds: Optional[FlagThresholds] = None,
    retrieval_top_k: int = DEFAULT_RETRIEVAL_TOP_K,
    preparer: str = AGENT_NAME,
    now: Callable[[], str] = _default_now,
) -> CloseVarianceRun:
    if thresholds is None:
        thresholds = FlagThresholds()

    connector = FileBudgetActualConnector(
        source_system=source_system,
        budget_folder=budget_folder,
        actuals_folder=actuals_folder,
    )
    lines = connector.fetch_lines(period=period)
    budget = [line for line in lines if line.source_capability == "budget"]
    actuals = [line for line in lines if line.source_capability == "actuals"]

    if not budget and not actuals:
        raise ValueError(f"no budget or actuals lines found for period {period!r}")

    currencies = sorted({line.currency for line in lines})
    if len(currencies) > 1:
        raise ValueError(
            f"period {period!r} mixes currencies {currencies}; "
            "multi-currency variance analysis is out of scope"
        )
    currency = currencies[0]

    audit_log.append(AuditEvent(
        timestamp=now(),
        agent=AGENT_NAME,
        action="budget_actuals_retrieved",
        actor=preparer,
        inputs={
            "source_system": source_system,
            "period": period,
            "budget_folder": str(budget_folder),
            "actuals_folder": str(actuals_folder),
        },
        output={
            "budget_count": len(budget),
            "actual_count": len(actuals),
            "currency": currency,
            "budget_lines": [_serialize_line(line) for line in budget],
            "actual_lines": [_serialize_line(line) for line in actuals],
        },
    ))

    line_variances = compute_variances(
        budget, actuals, period=period, currency=currency, thresholds=thresholds
    )
    flagged = [lv for lv in line_variances if lv.flagged]

    audit_log.append(AuditEvent(
        timestamp=now(),
        agent=AGENT_NAME,
        action="variances_computed",
        actor=preparer,
        inputs={"thresholds": thresholds.to_dict()},
        output={
            "line_count": len(line_variances),
            "flagged_count": len(flagged),
            "flagged": [
                {
                    "account": lv.account,
                    "line_item": lv.line_item,
                    "budget_amount": str(lv.budget_amount),
                    "actual_amount": str(lv.actual_amount),
                    "variance": str(lv.variance),
                    "pct_variance": str(lv.pct_variance) if lv.pct_variance is not None else None,
                    "direction": lv.direction,
                    "flag_reasons": lv.flag_reasons,
                }
                for lv in flagged
            ],
        },
    ))

    explanations: list[VarianceExplanation] = []
    explanation_result: Optional[ExplanationResult] = None
    skipped_reason: Optional[str] = None
    expl_model: Optional[str] = None
    expl_prompt_hash: Optional[str] = None
    expl_chunk_ids: list[str] = []
    expl_citations: list[str] = []

    if not flagged:
        skipped_reason = "no_flagged_variances"
        audit_log.append(AuditEvent(
            timestamp=now(),
            agent=AGENT_NAME,
            action="variance_explanations_skipped",
            actor=preparer,
            inputs={},
            output={"reason": skipped_reason},
        ))
    else:
        chunks = _retrieve_context(knowledge_base, flagged, retrieval_top_k)
        expl_chunk_ids = [r.chunk.chunk_id for r in chunks]
        expl_citations = [r.chunk.citation for r in chunks]

        audit_log.append(AuditEvent(
            timestamp=now(),
            agent=AGENT_NAME,
            action="explanation_context_retrieved",
            actor=preparer,
            inputs={"flagged_count": len(flagged), "top_k": retrieval_top_k},
            output={
                "grounded": bool(chunks),
                "chunk_ids": expl_chunk_ids,
                "citations": expl_citations,
            },
        ))

        if client is None:
            import anthropic
            client = anthropic.Anthropic()

        explanation_result = explain.draft_explanations(
            client, flagged, chunks, model=model, effort=effort
        )
        expl_model = explanation_result.model
        expl_prompt_hash = explanation_result.prompt_hash

        if explanation_result.explanations is None:
            if explanation_result.refused:
                skipped_reason = "explanation_refused"
                action = "variance_explanations_refused"
                output = {"refusal_category": explanation_result.refusal_category}
            else:
                skipped_reason = "explanation_failed"
                action = "variance_explanations_failed"
                output = {"parse_error": explanation_result.parse_error}
            audit_log.append(AuditEvent(
                timestamp=now(),
                agent=AGENT_NAME,
                action=action,
                actor=preparer,
                inputs={"chunk_ids": expl_chunk_ids},
                output=output,
                model=expl_model,
                prompt_hash=expl_prompt_hash,
            ))
        else:
            explanations = explanation_result.explanations
            audit_log.append(AuditEvent(
                timestamp=now(),
                agent=AGENT_NAME,
                action="variance_explanations_drafted",
                actor=preparer,
                inputs={"chunk_ids": expl_chunk_ids},
                output={
                    "citations": expl_citations,
                    "explanations": [e.to_dict() for e in explanations],
                },
                model=expl_model,
                prompt_hash=expl_prompt_hash,
            ))

    report = VarianceReport(
        period=period,
        source_system=source_system,
        generated_at=now(),
        currency=currency,
        thresholds=thresholds.to_dict(),
        line_variances=line_variances,
        explanations=explanations,
        explanations_skipped_reason=skipped_reason,
        model=expl_model,
        explanation_prompt_hash=expl_prompt_hash,
        explanation_chunk_ids=expl_chunk_ids,
        explanation_citations=expl_citations,
    )
    report_dict = report.to_dict()

    audit_log.append(AuditEvent(
        timestamp=now(),
        agent=AGENT_NAME,
        action="variance_report_generated",
        actor=preparer,
        inputs={},
        output=report_dict["summary"],
    ))

    approval_request = approval_queue.submit(
        agent=AGENT_NAME,
        action="variance_report",
        payload=report_dict,
        preparer=preparer,
        timestamp=now(),
    )

    return CloseVarianceRun(
        report=report,
        approval_request=approval_request,
        explanation_result=explanation_result,
    )
