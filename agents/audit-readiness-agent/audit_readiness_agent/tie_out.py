"""Deterministic tie-out: match a PBC item to evidence already recorded in a
reconciliation-agent run's audit log.

There is no explicit "run id" linking a reconciliation-agent run's events
together, so runs are recovered by adjacency: a run starts at each
`transactions_retrieved` event and includes everything up to (not including)
the next one. A run matches a PBC item when its recorded [start_date,
end_date] window covers the item's [period_start, period_end] window (and
its source_system, if the item specifies one). No LLM involvement — this is
plain code so results are reproducible and testable, same as
reconciliation-agent's matching.py.
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from audit_log import AuditEvent, AuditLogStore

from .models import PBCItem, TieOutEntry, TieOutResult

SUPPORTED_EVIDENCE_TYPES = {"bank_reconciliation"}

_RUN_START_ACTION = "transactions_retrieved"
_REPORT_ACTION = "report_generated"


@dataclass
class _Run:
    events: list[AuditEvent] = field(default_factory=list)

    @property
    def start_event(self) -> AuditEvent:
        return self.events[0]

    def find(self, action: str) -> Optional[AuditEvent]:
        for event in self.events:
            if event.action == action:
                return event
        return None

    @property
    def source_system(self) -> Optional[str]:
        return self.start_event.inputs.get("source_system")

    @property
    def period_start(self) -> Optional[date]:
        return _parse_date(self.start_event.inputs.get("start_date"))

    @property
    def period_end(self) -> Optional[date]:
        return _parse_date(self.start_event.inputs.get("end_date"))

    @property
    def window_span_days(self) -> float:
        if self.period_start is None or self.period_end is None:
            return float("inf")
        return (self.period_end - self.period_start).days


def _parse_date(raw: Optional[str]) -> Optional[date]:
    return date.fromisoformat(raw) if raw else None


def _group_into_runs(events: list[AuditEvent]) -> list[_Run]:
    runs: list[_Run] = []
    for event in events:
        if event.action == _RUN_START_ACTION:
            runs.append(_Run(events=[event]))
        elif runs:
            runs[-1].events.append(event)
    return runs


def _covers(run: _Run, item: PBCItem) -> bool:
    if run.period_start is not None and run.period_start > item.period_start:
        return False
    if run.period_end is not None and run.period_end < item.period_end:
        return False
    if item.source_system is not None and run.source_system != item.source_system:
        return False
    return True


def _last_approval_status(run: _Run) -> Optional[str]:
    status = None
    for event in run.events:
        if event.approval_status is not None:
            status = event.approval_status
    return status


def find_evidence(pbc_item: PBCItem, evidence_audit_log: AuditLogStore) -> TieOutResult:
    db_path = evidence_audit_log.db_path

    if pbc_item.evidence_type not in SUPPORTED_EVIDENCE_TYPES:
        return TieOutResult(
            pbc_item_id=pbc_item.item_id,
            found=False,
            gap_reason=(
                f"evidence_type {pbc_item.evidence_type!r} is not supported "
                f"(supported: {sorted(SUPPORTED_EVIDENCE_TYPES)})"
            ),
            evidence_source_db_path=db_path,
        )

    runs = _group_into_runs(evidence_audit_log.get_all())
    if not runs:
        return TieOutResult(
            pbc_item_id=pbc_item.item_id,
            found=False,
            gap_reason="no reconciliation-agent runs found in evidence audit log",
            evidence_source_db_path=db_path,
        )

    candidates = [run for run in runs if _covers(run, pbc_item)]
    candidates = [run for run in candidates if run.find(_REPORT_ACTION) is not None]

    if not candidates:
        return TieOutResult(
            pbc_item_id=pbc_item.item_id,
            found=False,
            gap_reason=(
                "no reconciliation-agent run covers the requested period "
                f"{pbc_item.period_start.isoformat()}..{pbc_item.period_end.isoformat()}"
            ),
            evidence_source_db_path=db_path,
        )

    best = min(candidates, key=lambda run: (run.window_span_days, -run.start_event.id))
    report_event = best.find(_REPORT_ACTION)

    entry = TieOutEntry(
        audit_event_ids=[best.start_event.id, report_event.id],
        evidence_agent=best.start_event.agent,
        period_start=best.period_start,
        period_end=best.period_end,
        source_system=best.source_system,
        summary=report_event.output,
        approval_status=_last_approval_status(best),
    )

    return TieOutResult(
        pbc_item_id=pbc_item.item_id,
        found=True,
        entries=[entry],
        evidence_source_db_path=db_path,
    )
