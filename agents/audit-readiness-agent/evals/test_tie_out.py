from datetime import date

from audit_readiness_agent import PBCItem, find_evidence
from fixtures import EVIDENCE_SOURCE_SYSTEM, build_evidence_log


def _item(**overrides) -> PBCItem:
    defaults = dict(
        item_id="PBC-1",
        description="Provide the July 2026 bank reconciliation with supporting evidence.",
        period_start=date(2026, 7, 1),
        period_end=date(2026, 7, 31),
        evidence_type="bank_reconciliation",
        source_system=EVIDENCE_SOURCE_SYSTEM,
    )
    defaults.update(overrides)
    return PBCItem(**defaults)


def test_period_hit_finds_evidence():
    evidence_log = build_evidence_log()
    result = find_evidence(_item(), evidence_log)

    assert result.found is True
    assert result.gap_reason is None
    assert len(result.entries) == 1

    entry = result.entries[0]
    assert entry.evidence_agent == "reconciliation-agent"
    assert entry.approval_status == "approved"
    assert entry.summary["matched_exact_count"] == 2
    assert entry.summary["matched_tolerance_count"] == 1
    assert len(entry.audit_event_ids) == 2

    evidence_log.close()


def test_period_miss_is_a_gap():
    evidence_log = build_evidence_log()
    august_item = _item(item_id="PBC-2", period_start=date(2026, 8, 1), period_end=date(2026, 8, 31))
    result = find_evidence(august_item, evidence_log)

    assert result.found is False
    assert result.entries == []
    assert "2026-08-01" in result.gap_reason
    assert "2026-08-31" in result.gap_reason

    evidence_log.close()


def test_unknown_evidence_type_is_a_gap_regardless_of_log_contents():
    evidence_log = build_evidence_log()
    item = _item(item_id="PBC-3", evidence_type="fixed_asset_rollforward")
    result = find_evidence(item, evidence_log)

    assert result.found is False
    assert "fixed_asset_rollforward" in result.gap_reason

    evidence_log.close()


def test_source_system_mismatch_is_a_gap():
    evidence_log = build_evidence_log()
    item = _item(item_id="PBC-4", source_system="other_co")
    result = find_evidence(item, evidence_log)

    assert result.found is False

    evidence_log.close()
