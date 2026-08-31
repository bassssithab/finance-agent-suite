"""Orchestrates one journal-entry segregation-of-duties control test: connector
-> deterministic SoD test -> optional cited deficiency narratives -> approvals.

Every step writes to the audit log, including which entries were tested and the
deterministic reason each violation was flagged, so the run can be reconstructed
as auditor evidence (CLAUDE.md golden rule #3). The agent never reads the
journal-entry CSV itself — it goes through `platform/connectors` (rule #1) — and
never treats the report as final without a human approval (rule #2): this
function only ever produces a draft control-test report for review.

`autonomy: draft_only`. Flagged violations do NOT stop submission — the report
still goes to a human, carrying the flags and the drafted narratives, because
the agent does not get to decide (rule #2). A model refusal or an unparseable
response also does not stop submission: the report goes through without
narratives, with `narratives_skipped_reason` set.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Union

from approvals import ApprovalQueue, ApprovalRequest
from audit_log import AuditEvent, AuditLogStore
from connectors import FileJournalEntryConnector, JournalEntry
from knowledge import KnowledgeBase, SearchResult

from . import narrate
from .models import (
    ControlPolicy,
    ControlTestReport,
    ControlTestResult,
    DeficiencyNarrative,
)
from .narrate import FlaggedPair, NarrativeResult, describe_violation
from .sod import check_segregation_of_duties

AGENT_NAME = "controls-sox-agent"
CONTROL_ID = "JE-SOD-001"
CONTROL_NAME = "Segregation of duties on journal-entry approval"
KNOWLEDGE_CORPUS = "internal_controls_policy"
DEFAULT_RETRIEVAL_TOP_K = 3


def _default_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _serialize_entry(entry: JournalEntry) -> dict:
    return {
        "source_capability": entry.source_capability,
        "entry_id": entry.entry_id,
        "date": entry.date.isoformat(),
        "account": entry.account,
        "amount": str(entry.amount),
        "currency": entry.currency,
        "preparer": entry.preparer,
        "approver_1": entry.approver_1,
        "approver_2": entry.approver_2,
        "raw": entry.raw,
    }


@dataclass
class ControlTestRun:
    report: ControlTestReport
    approval_request: ApprovalRequest
    narrative_result: Optional[NarrativeResult]  # None when nothing was flagged


def _flagged_pairs(results: list[ControlTestResult]) -> list[FlaggedPair]:
    return [(result, violation) for result in results for violation in result.violations]


def _retrieve_context(
    knowledge_base: KnowledgeBase, flagged: list[FlaggedPair], top_k: int
) -> list[SearchResult]:
    """One search per flagged exception, de-duplicated by chunk_id, order
    preserved (same shape as close_agent._retrieve_context)."""
    seen: set[str] = set()
    chunks: list[SearchResult] = []
    for result, violation in flagged:
        for hit in knowledge_base.search(describe_violation(result, violation), top_k=top_k):
            if hit.chunk.chunk_id not in seen:
                seen.add(hit.chunk.chunk_id)
                chunks.append(hit)
    return chunks


def run_journal_entry_control_test(
    *,
    source_system: str,
    entries_folder: Union[str, Path],
    knowledge_base: KnowledgeBase,
    audit_log: AuditLogStore,
    approval_queue: ApprovalQueue,
    client=None,
    model: str = narrate.DEFAULT_MODEL,
    effort: str = narrate.DEFAULT_EFFORT,
    policy: Optional[ControlPolicy] = None,
    retrieval_top_k: int = DEFAULT_RETRIEVAL_TOP_K,
    preparer: str = AGENT_NAME,
    now: Callable[[], str] = _default_now,
) -> ControlTestRun:
    if policy is None:
        policy = ControlPolicy()

    connector = FileJournalEntryConnector(source_system=source_system, folder=entries_folder)
    entries = connector.fetch_entries()

    if not entries:
        raise ValueError(f"no journal entries found in {entries_folder!r}")

    currencies = sorted({entry.currency for entry in entries})
    if len(currencies) > 1:
        raise ValueError(
            f"journal entries mix currencies {currencies}; "
            "multi-currency control testing is out of scope"
        )
    currency = currencies[0]

    audit_log.append(AuditEvent(
        timestamp=now(),
        agent=AGENT_NAME,
        action="journal_entries_retrieved",
        actor=preparer,
        inputs={"source_system": source_system, "entries_folder": str(entries_folder)},
        output={
            "entry_count": len(entries),
            "currency": currency,
            "entries": [_serialize_entry(entry) for entry in entries],
        },
    ))

    results = check_segregation_of_duties(entries, policy)
    flagged = _flagged_pairs(results)

    audit_log.append(AuditEvent(
        timestamp=now(),
        agent=AGENT_NAME,
        action="sod_control_tested",
        actor=preparer,
        inputs={"control_id": CONTROL_ID, "policy": policy.to_dict()},
        output={
            "entries_tested": [r.entry_id for r in results],
            "passed_count": sum(1 for r in results if r.passed),
            "violation_count": len(flagged),
            "violations": [
                {
                    "entry_id": result.entry_id,
                    "account": result.account,
                    "amount": str(result.amount),
                    "preparer": result.preparer,
                    "approver_1": result.approver_1,
                    "approver_2": result.approver_2,
                    "dual_approval_required": result.dual_approval_required,
                    "code": violation.code,
                    "detail": violation.detail,
                }
                for result, violation in flagged
            ],
        },
    ))

    narratives: list[DeficiencyNarrative] = []
    narrative_result: Optional[NarrativeResult] = None
    skipped_reason: Optional[str] = None
    nar_model: Optional[str] = None
    nar_prompt_hash: Optional[str] = None
    nar_chunk_ids: list[str] = []
    nar_citations: list[str] = []

    if not flagged:
        skipped_reason = "no_violations"
        audit_log.append(AuditEvent(
            timestamp=now(),
            agent=AGENT_NAME,
            action="deficiency_narratives_skipped",
            actor=preparer,
            inputs={},
            output={"reason": skipped_reason},
        ))
    else:
        chunks = _retrieve_context(knowledge_base, flagged, retrieval_top_k)
        nar_chunk_ids = [hit.chunk.chunk_id for hit in chunks]
        nar_citations = [hit.chunk.citation for hit in chunks]

        audit_log.append(AuditEvent(
            timestamp=now(),
            agent=AGENT_NAME,
            action="deficiency_context_retrieved",
            actor=preparer,
            inputs={"violation_count": len(flagged), "top_k": retrieval_top_k},
            output={
                "grounded": bool(chunks),
                "chunk_ids": nar_chunk_ids,
                "citations": nar_citations,
            },
        ))

        if client is None:
            import anthropic
            client = anthropic.Anthropic()

        narrative_result = narrate.draft_deficiency_narratives(
            client, flagged, chunks, model=model, effort=effort
        )
        nar_model = narrative_result.model
        nar_prompt_hash = narrative_result.prompt_hash

        if narrative_result.narratives is None:
            if narrative_result.refused:
                skipped_reason = "deficiency_narratives_refused"
                action = "deficiency_narratives_refused"
                output = {"refusal_category": narrative_result.refusal_category}
            else:
                skipped_reason = "deficiency_narratives_failed"
                action = "deficiency_narratives_failed"
                output = {"parse_error": narrative_result.parse_error}
            audit_log.append(AuditEvent(
                timestamp=now(),
                agent=AGENT_NAME,
                action=action,
                actor=preparer,
                inputs={"chunk_ids": nar_chunk_ids},
                output=output,
                model=nar_model,
                prompt_hash=nar_prompt_hash,
            ))
        else:
            narratives = narrative_result.narratives
            audit_log.append(AuditEvent(
                timestamp=now(),
                agent=AGENT_NAME,
                action="deficiency_narratives_drafted",
                actor=preparer,
                inputs={"chunk_ids": nar_chunk_ids},
                output={
                    "citations": nar_citations,
                    "narratives": [n.to_dict() for n in narratives],
                },
                model=nar_model,
                prompt_hash=nar_prompt_hash,
            ))

    report = ControlTestReport(
        control_id=CONTROL_ID,
        control_name=CONTROL_NAME,
        source_system=source_system,
        generated_at=now(),
        currency=currency,
        policy=policy.to_dict(),
        results=results,
        narratives=narratives,
        narratives_skipped_reason=skipped_reason,
        model=nar_model,
        narrative_prompt_hash=nar_prompt_hash,
        narrative_chunk_ids=nar_chunk_ids,
        narrative_citations=nar_citations,
    )
    report_dict = report.to_dict()

    audit_log.append(AuditEvent(
        timestamp=now(),
        agent=AGENT_NAME,
        action="control_test_report_generated",
        actor=preparer,
        inputs={"control_id": CONTROL_ID},
        output=report_dict["summary"],
    ))

    approval_request = approval_queue.submit(
        agent=AGENT_NAME,
        action="control_test_report",
        payload=report_dict,
        preparer=preparer,
        timestamp=now(),
    )

    return ControlTestRun(
        report=report,
        approval_request=approval_request,
        narrative_result=narrative_result,
    )
