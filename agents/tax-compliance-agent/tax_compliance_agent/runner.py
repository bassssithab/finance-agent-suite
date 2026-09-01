"""Orchestrates one period-end VAT provision: connector -> deterministic
calculation -> filing-support narrative -> approvals.

Every step writes to the audit log, including the by-treatment breakdown and the
deterministic reason each anomaly was flagged, so the run can be reconstructed
as auditor evidence (CLAUDE.md golden rule #3). The agent never reads the
transaction CSV itself — it goes through `platform/connectors` (rule #1) — and
never treats the provision as a filed return without a human approval (rule #2):
this function only ever produces a draft provision report for review, and the
return itself is filed by a qualified person.

`autonomy: draft_only`. Flagged anomalies do NOT stop submission — the report
still goes to a human, carrying the anomalies and the drafted narrative. A model
refusal or an unparseable narrative also does not stop submission: the
deterministic calculation is complete on its own, so the report goes through
without a narrative, with `narrative_skipped_reason` set.

Like agents/fpa-agent, this agent ALWAYS makes one Claude call — there is always
a VAT position to summarise, anomalies or not.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Union

from approvals import ApprovalQueue, ApprovalRequest
from audit_log import AuditEvent, AuditLogStore
from connectors import FileVatTransactionConnector, VatTransaction
from knowledge import KnowledgeBase, SearchResult

from . import narrate
from .models import (
    FilingSupportNarrative,
    ProvisionPolicy,
    VatProvisionReport,
    serialize_by_treatment,
)
from .narrate import NarrativeResult, describe_anomaly, describe_position
from .provision import compute_provision

AGENT_NAME = "tax-compliance-agent"
KNOWLEDGE_CORPUS = "vat_policy"
DEFAULT_RETRIEVAL_TOP_K = 3


def _default_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _serialize_txn(txn: VatTransaction) -> dict:
    return {
        "source_capability": txn.source_capability,
        "transaction_id": txn.transaction_id,
        "date": txn.date.isoformat(),
        "transaction_type": txn.transaction_type,
        "amount": str(txn.amount),
        "vat_treatment": txn.vat_treatment,
        "vat_rate": str(txn.vat_rate) if txn.vat_rate is not None else None,
        "currency": txn.currency,
        "raw": txn.raw,
    }


@dataclass
class VatProvisionRun:
    report: VatProvisionReport
    approval_request: ApprovalRequest
    narrative_result: NarrativeResult


def _retrieve_context(
    knowledge_base: KnowledgeBase, summary: dict, anomalies: list, top_k: int
) -> list[SearchResult]:
    """One general position query plus one per anomaly, de-duplicated by
    chunk_id, order preserved (same shape as close_agent._retrieve_context)."""
    queries = [describe_position(summary)]
    queries.extend(describe_anomaly(a) for a in anomalies)

    seen: set[str] = set()
    chunks: list[SearchResult] = []
    for query in queries:
        for result in knowledge_base.search(query, top_k=top_k):
            if result.chunk.chunk_id not in seen:
                seen.add(result.chunk.chunk_id)
                chunks.append(result)
    return chunks


def run_vat_provision(
    *,
    source_system: str,
    transactions_folder: Union[str, Path],
    knowledge_base: KnowledgeBase,
    audit_log: AuditLogStore,
    approval_queue: ApprovalQueue,
    policy: Optional[ProvisionPolicy] = None,
    period_label: Optional[str] = None,
    client=None,
    model: str = narrate.DEFAULT_MODEL,
    effort: str = narrate.DEFAULT_EFFORT,
    retrieval_top_k: int = DEFAULT_RETRIEVAL_TOP_K,
    preparer: str = AGENT_NAME,
    now: Callable[[], str] = _default_now,
) -> VatProvisionRun:
    if policy is None:
        policy = ProvisionPolicy()

    connector = FileVatTransactionConnector(
        source_system=source_system, folder=transactions_folder
    )
    transactions = connector.fetch_transactions()

    if not transactions:
        raise ValueError(f"no VAT transactions found in {transactions_folder!r}")

    currencies = sorted({txn.currency for txn in transactions})
    if len(currencies) > 1:
        raise ValueError(
            f"transactions mix currencies {currencies}; "
            "multi-currency VAT provisioning is out of scope"
        )
    currency = currencies[0]

    dates = sorted(txn.date for txn in transactions)
    date_range = {"from": dates[0].isoformat(), "to": dates[-1].isoformat()}
    if period_label is None:
        period_label = f"{date_range['from']} to {date_range['to']}"

    audit_log.append(AuditEvent(
        timestamp=now(),
        agent=AGENT_NAME,
        action="vat_transactions_retrieved",
        actor=preparer,
        inputs={
            "source_system": source_system,
            "transactions_folder": str(transactions_folder),
            "period_label": period_label,
        },
        output={
            "transaction_count": len(transactions),
            "currency": currency,
            "date_range": date_range,
            "transactions": [_serialize_txn(txn) for txn in transactions],
        },
    ))

    # ---- deterministic calculation (no LLM) ------------------------------
    result = compute_provision(transactions, policy=policy)

    audit_log.append(AuditEvent(
        timestamp=now(),
        agent=AGENT_NAME,
        action="vat_provision_computed",
        actor=preparer,
        inputs={"policy": policy.to_dict()},
        output={
            "transaction_count": len(result.computed_transactions),
            "output_vat_total": str(result.output_vat_total),
            "input_vat_total": str(result.input_vat_total),
            "net_vat": str(result.net_vat),
            "position": result.position,
            "by_treatment": serialize_by_treatment(result.by_treatment),
            "transactions_excluded_from_totals": result.excluded_transaction_ids,
            "anomaly_count": len(result.anomalies),
            "anomalies": [a.to_dict() for a in result.anomalies],
        },
    ))

    report = VatProvisionReport(
        source_system=source_system,
        generated_at=now(),
        currency=currency,
        period_label=period_label,
        date_range=date_range,
        policy=policy.to_dict(),
        computed_transactions=result.computed_transactions,
        by_treatment=result.by_treatment,
        output_vat_total=result.output_vat_total,
        input_vat_total=result.input_vat_total,
        net_vat=result.net_vat,
        position=result.position,
        anomalies=result.anomalies,
        narrative=None,
        narrative_skipped_reason=None,
        model=None,
        narrative_prompt_hash=None,
        narrative_chunk_ids=[],
        narrative_citations=[],
    )
    summary = report.summary()

    # ---- narrative (always drafted) ------------------------------------
    chunks = _retrieve_context(knowledge_base, summary, result.anomalies, retrieval_top_k)
    nar_chunk_ids = [r.chunk.chunk_id for r in chunks]
    nar_citations = [r.chunk.citation for r in chunks]

    audit_log.append(AuditEvent(
        timestamp=now(),
        agent=AGENT_NAME,
        action="filing_guidance_context_retrieved",
        actor=preparer,
        inputs={"anomaly_count": len(result.anomalies), "top_k": retrieval_top_k},
        output={
            "grounded": bool(chunks),
            "chunk_ids": nar_chunk_ids,
            "citations": nar_citations,
        },
    ))

    if client is None:
        import anthropic
        client = anthropic.Anthropic()

    narrative_result = narrate.draft_filing_support_narrative(
        client, summary, result.by_treatment, summary["anomalies"], chunks,
        model=model, effort=effort,
    )

    narrative: Optional[FilingSupportNarrative] = None
    skipped_reason: Optional[str] = None

    if narrative_result.narrative is None:
        if narrative_result.refused:
            skipped_reason = "narrative_refused"
            action = "filing_support_narrative_refused"
            output = {"refusal_category": narrative_result.refusal_category}
        else:
            skipped_reason = "narrative_failed"
            action = "filing_support_narrative_failed"
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
            action="filing_support_narrative_drafted",
            actor=preparer,
            inputs={"chunk_ids": nar_chunk_ids},
            output={"citations": nar_citations, "narrative": narrative.to_dict()},
            model=narrative_result.model,
            prompt_hash=narrative_result.prompt_hash,
        ))

    report = VatProvisionReport(
        source_system=source_system,
        generated_at=report.generated_at,
        currency=currency,
        period_label=period_label,
        date_range=date_range,
        policy=policy.to_dict(),
        computed_transactions=result.computed_transactions,
        by_treatment=result.by_treatment,
        output_vat_total=result.output_vat_total,
        input_vat_total=result.input_vat_total,
        net_vat=result.net_vat,
        position=result.position,
        anomalies=result.anomalies,
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
        action="vat_provision_report_generated",
        actor=preparer,
        inputs={},
        output=report_dict["summary"],
    ))

    approval_request = approval_queue.submit(
        agent=AGENT_NAME,
        action="vat_provision",
        payload=report_dict,
        preparer=preparer,
        timestamp=now(),
    )

    return VatProvisionRun(
        report=report,
        approval_request=approval_request,
        narrative_result=narrative_result,
    )
