"""Orchestrates one regulatory-change impact triage: free-text requirement +
controls connector -> deterministic keyword/category match -> impact-assessment
narrative -> approvals.

Every step writes to the audit log, including which controls the keyword match
surfaced and the deterministic reason a gap was flagged, so the run can be
reconstructed as auditor evidence (CLAUDE.md golden rule #3). The agent never
reads the controls CSV itself — it goes through `platform/connectors` (rule #1)
— and never treats the triage as a compliance determination (rule #2): this
function only ever produces a draft triage report for a qualified
legal/compliance reviewer.

`autonomy: draft_only`. A flagged gap does NOT stop submission — the report
still goes to a human, carrying the flag and the drafted assessment. A model
refusal or an unparseable narrative also does not stop submission: the
deterministic triage is complete on its own, so the report goes through without
a narrative, with `narrative_skipped_reason` set.

Like agents/fpa-agent and agents/tax-compliance-agent, this agent ALWAYS makes
one Claude call — there is always a triage result to summarise.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Union

from approvals import ApprovalQueue, ApprovalRequest
from audit_log import AuditEvent, AuditLogStore
from connectors import FileInternalControlConnector, InternalControl
from knowledge import KnowledgeBase, SearchResult

from . import narrate
from .models import ImpactNarrative, TriagePolicy, TriageReport
from .narrate import NarrativeResult, describe_control, describe_requirement
from .triage import assess_impact

AGENT_NAME = "regulatory-change-agent"
KNOWLEDGE_CORPUS = "regulatory_guidance"
DEFAULT_RETRIEVAL_TOP_K = 3


def _default_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _serialize_control(control: InternalControl) -> dict:
    return {
        "source_capability": control.source_capability,
        "control_id": control.control_id,
        "description": control.description,
        "category": control.category,
        "raw": control.raw,
    }


@dataclass
class TriageRun:
    report: TriageReport
    approval_request: ApprovalRequest
    narrative_result: NarrativeResult


def _retrieve_context(
    knowledge_base: KnowledgeBase, requirement_text: str, verdict: str,
    surfaced: list, top_k: int,
) -> list[SearchResult]:
    """One requirement query plus one per surfaced control, de-duplicated by
    chunk_id, order preserved (same shape as close_agent._retrieve_context)."""
    queries = [describe_requirement(requirement_text, verdict)]
    queries.extend(describe_control(cr) for cr in surfaced)

    seen: set[str] = set()
    chunks: list[SearchResult] = []
    for query in queries:
        for result in knowledge_base.search(query, top_k=top_k):
            if result.chunk.chunk_id not in seen:
                seen.add(result.chunk.chunk_id)
                chunks.append(result)
    return chunks


def run_change_triage(
    *,
    source_system: str,
    requirement_text: str,
    controls_folder: Union[str, Path],
    knowledge_base: KnowledgeBase,
    audit_log: AuditLogStore,
    approval_queue: ApprovalQueue,
    requirement_reference: Optional[str] = None,
    policy: Optional[TriagePolicy] = None,
    client=None,
    model: str = narrate.DEFAULT_MODEL,
    effort: str = narrate.DEFAULT_EFFORT,
    retrieval_top_k: int = DEFAULT_RETRIEVAL_TOP_K,
    preparer: str = AGENT_NAME,
    now: Callable[[], str] = _default_now,
) -> TriageRun:
    if policy is None:
        policy = TriagePolicy()

    if not (requirement_text or "").strip():
        raise ValueError("requirement_text is empty; nothing to triage")

    audit_log.append(AuditEvent(
        timestamp=now(),
        agent=AGENT_NAME,
        action="regulatory_change_received",
        actor=preparer,
        inputs={"source_system": source_system},
        output={
            "requirement_text": requirement_text,
            "requirement_reference": requirement_reference,
        },
    ))

    connector = FileInternalControlConnector(source_system=source_system, folder=controls_folder)
    controls = connector.fetch_controls()

    if not controls:
        raise ValueError(f"no internal controls found in {controls_folder!r}")

    audit_log.append(AuditEvent(
        timestamp=now(),
        agent=AGENT_NAME,
        action="controls_retrieved",
        actor=preparer,
        inputs={"controls_folder": str(controls_folder)},
        output={
            "control_count": len(controls),
            "controls": [_serialize_control(c) for c in controls],
        },
    ))

    # ---- deterministic keyword/category triage (no LLM) ------------------
    result = assess_impact(requirement_text, controls, policy=policy)

    audit_log.append(AuditEvent(
        timestamp=now(),
        agent=AGENT_NAME,
        action="impact_triaged",
        actor=preparer,
        inputs={"policy": policy.to_dict()},
        output={
            "control_count": len(controls),
            "relevant_control_count": len(result.relevant_controls),
            "coverage_verdict": result.coverage_verdict,
            "gap_flagged": result.gap_flagged,
            "flag_reasons": result.flag_reasons,
            "surfaced_controls": [
                {
                    "control_id": cr.control_id,
                    "category": cr.category,
                    "score": cr.score,
                    "matched_terms": cr.matched_terms,
                    "category_match": cr.category_match,
                }
                for cr in result.relevant_controls
            ],
        },
    ))

    # ---- narrative (always drafted) -----------------------------------
    chunks = _retrieve_context(
        knowledge_base, requirement_text, result.coverage_verdict,
        result.relevant_controls, retrieval_top_k,
    )
    nar_chunk_ids = [r.chunk.chunk_id for r in chunks]
    nar_citations = [r.chunk.citation for r in chunks]

    audit_log.append(AuditEvent(
        timestamp=now(),
        agent=AGENT_NAME,
        action="triage_context_retrieved",
        actor=preparer,
        inputs={"surfaced_count": len(result.relevant_controls), "top_k": retrieval_top_k},
        output={
            "grounded": bool(chunks),
            "chunk_ids": nar_chunk_ids,
            "citations": nar_citations,
        },
    ))

    if client is None:
        import anthropic
        client = anthropic.Anthropic()

    narrative_result = narrate.draft_impact_assessment(
        client,
        requirement_text=requirement_text,
        requirement_reference=requirement_reference,
        policy=policy.to_dict(),
        surfaced=result.relevant_controls,
        coverage_verdict=result.coverage_verdict,
        gap_flagged=result.gap_flagged,
        flag_reasons=result.flag_reasons,
        control_count=len(controls),
        chunks=chunks,
        model=model,
        effort=effort,
    )

    narrative: Optional[ImpactNarrative] = None
    skipped_reason: Optional[str] = None

    if narrative_result.narrative is None:
        if narrative_result.refused:
            skipped_reason = "narrative_refused"
            action = "impact_assessment_refused"
            output = {"refusal_category": narrative_result.refusal_category}
        else:
            skipped_reason = "narrative_failed"
            action = "impact_assessment_failed"
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
            action="impact_assessment_drafted",
            actor=preparer,
            inputs={"chunk_ids": nar_chunk_ids},
            output={"citations": nar_citations, "narrative": narrative.to_dict()},
            model=narrative_result.model,
            prompt_hash=narrative_result.prompt_hash,
        ))

    report = TriageReport(
        requirement_text=requirement_text,
        requirement_reference=requirement_reference,
        source_system=source_system,
        generated_at=now(),
        policy=policy.to_dict(),
        control_count=len(controls),
        relevances=result.relevances,
        relevant_controls=result.relevant_controls,
        coverage_verdict=result.coverage_verdict,
        gap_flagged=result.gap_flagged,
        flag_reasons=result.flag_reasons,
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
        action="triage_report_generated",
        actor=preparer,
        inputs={},
        output=report_dict["summary"],
    ))

    approval_request = approval_queue.submit(
        agent=AGENT_NAME,
        action="change_triage",
        payload=report_dict,
        preparer=preparer,
        timestamp=now(),
    )

    return TriageRun(
        report=report,
        approval_request=approval_request,
        narrative_result=narrative_result,
    )
