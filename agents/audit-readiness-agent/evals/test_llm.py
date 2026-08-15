from datetime import date

from audit_readiness_agent import PBCItem
from audit_readiness_agent.llm import NO_EVIDENCE_MARKER, build_user_prompt, draft_response
from audit_readiness_agent.models import TieOutEntry, TieOutResult
from fakes import refusal_response, text_response
from fixtures import BANK_REC_POLICY
from knowledge import KnowledgeBase

ITEM = PBCItem(
    item_id="PBC-1",
    description="Provide the July 2026 bank reconciliation with supporting evidence.",
    period_start=date(2026, 7, 1),
    period_end=date(2026, 7, 31),
    evidence_type="bank_reconciliation",
    source_system="sample_co",
)

FOUND_RESULT = TieOutResult(
    pbc_item_id="PBC-1",
    found=True,
    entries=[
        TieOutEntry(
            audit_event_ids=[1, 3],
            evidence_agent="reconciliation-agent",
            period_start=date(2026, 7, 1),
            period_end=date(2026, 7, 31),
            source_system="sample_co",
            summary={"matched_exact_count": 2, "difference": "1325.00"},
            approval_status="approved",
        ),
    ],
    evidence_source_db_path=":memory:",
)

GAP_RESULT = TieOutResult(
    pbc_item_id="PBC-1",
    found=False,
    gap_reason="no reconciliation-agent run covers the requested period",
    evidence_source_db_path=":memory:",
)


def _knowledge_chunks():
    kb = KnowledgeBase()
    kb.ingest([BANK_REC_POLICY])
    return kb.search("bank reconciliation policy", top_k=1)


def test_build_user_prompt_includes_evidence_citations_when_found():
    prompt = build_user_prompt(ITEM, FOUND_RESULT, [])
    assert "audit event 1" in prompt
    assert "audit event 3" in prompt
    assert NO_EVIDENCE_MARKER not in prompt


def test_build_user_prompt_includes_marker_when_no_evidence():
    prompt = build_user_prompt(ITEM, GAP_RESULT, [])
    assert NO_EVIDENCE_MARKER in prompt
    assert GAP_RESULT.gap_reason in prompt


def test_draft_response_success_with_evidence_and_knowledge():
    chunks = _knowledge_chunks()
    client = text_response("July 2026 is fully reconciled (reconciliation-agent audit event 3).")

    draft = draft_response(client, ITEM, FOUND_RESULT, chunks)

    assert draft.refused is False
    assert draft.tie_out_found is True
    assert draft.evidence_event_ids == [1, 3]
    assert draft.knowledge_chunk_ids == [chunks[0].chunk.chunk_id]
    assert "reconciliation-agent audit event 1" in draft.citations
    assert chunks[0].chunk.citation in draft.citations
    assert draft.response_text.startswith("July 2026")


def test_draft_response_refusal():
    client = refusal_response(category="cyber")

    draft = draft_response(client, ITEM, FOUND_RESULT, [])

    assert draft.refused is True
    assert draft.refusal_category == "cyber"
    assert draft.response_text is None
