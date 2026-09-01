from decimal import Decimal

from knowledge import Chunk, SearchResult

from tax_compliance_agent.narrate import (
    RECORD_FILING_SUPPORT_NARRATIVE_TOOL,
    SYSTEM_PROMPT,
    draft_filing_support_narrative,
)
from fakes import narrative_client, no_tool_call_client, refusal_client
from fixtures import FILING_CITATION, narrative_payload

TOOL_NAME = "record_filing_support_narrative"

SUMMARY = {
    "period_label": "2026-07-02 to 2026-07-20",
    "date_range": {"from": "2026-07-02", "to": "2026-07-20"},
    "output_vat_total": "24750.00",
    "input_vat_total": "10200.00",
    "net_vat": "14550.00",
    "position": "payable",
    "anomalies": [
        {"code": "treatment_rate_mismatch", "transaction_id": "TXN-5001",
         "detail": "transaction TXN-5001 is standard-rated but has no VAT rate recorded"},
    ],
    "transactions_excluded_from_totals": ["TXN-5003"],
}
BY_TREATMENT = {
    "standard-rated": {
        "sale": {"count": 2, "amount": Decimal("165000.00"), "vat": Decimal("24750.00")},
        "purchase": {"count": 2, "amount": Decimal("68000.00"), "vat": Decimal("10200.00")},
    },
    "zero-rated": {
        "sale": {"count": 1, "amount": Decimal("30000.00"), "vat": Decimal("0.00")},
        "purchase": {"count": 0, "amount": Decimal("0"), "vat": Decimal("0")},
    },
}


def chunk(doc_id="doc-x", position=0, text="At period end the business nets output VAT against input VAT."):
    c = Chunk(
        chunk_id=f"{doc_id}:{position}",
        doc_id=doc_id,
        doc_title="VAT Code: Period Return & Provision",
        corpus="vat_policy",
        position=position,
        text=text,
    )
    return SearchResult(chunk=c, score=1.0)


def test_tool_schema_requires_all_four_fields():
    props = RECORD_FILING_SUPPORT_NARRATIVE_TOOL["input_schema"]
    assert set(props["required"]) == {
        "position_summary", "anomaly_explanations", "specialist_review_needed", "citations"
    }


def test_system_prompt_forbids_asserting_the_filing_is_ready():
    p = SYSTEM_PROMPT.lower()
    assert "final and authoritative" in p
    assert "ready to submit" in p
    assert '"the return is ready"' in p
    assert '"everything ties out"' in p
    assert "tax specialist" in p
    assert "qualified tax professional" in p
    assert "not tax advice" in p
    assert "not for you to resolve or explain away" in p


def test_grounded_narrative_is_parsed():
    client = narrative_client(narrative_payload(
        anomaly_codes=["treatment_rate_mismatch"], specialist_review=True
    ))
    out = draft_filing_support_narrative(client, SUMMARY, BY_TREATMENT, SUMMARY["anomalies"], [chunk()])

    assert out.narrative is not None
    assert out.narrative.position_summary
    assert out.narrative.anomaly_explanations
    assert out.narrative.specialist_review_needed is True
    assert out.narrative.citations == [FILING_CITATION]
    assert out.refused is False and out.parse_error is None

    req = client.messages.last_request
    assert req["model"] == "claude-sonnet-5"
    assert req["tool_choice"] == {"type": "tool", "name": TOOL_NAME}
    assert req["output_config"] == {"effort": "medium"}


def test_ungrounded_run_passes_no_chunks_and_expects_empty_citations():
    client = narrative_client(narrative_payload(citation=None))
    out = draft_filing_support_narrative(client, SUMMARY, BY_TREATMENT, [], [])

    assert out.chunk_ids == []
    assert out.citations == []
    assert out.narrative.citations == []
    prompt = client.messages.last_request["messages"][0]["content"]
    assert "(no VAT filing-guidance excerpts were retrieved" in prompt


def test_user_prompt_states_figures_are_authoritative_and_lists_the_position():
    client = narrative_client(narrative_payload())
    draft_filing_support_narrative(client, SUMMARY, BY_TREATMENT, SUMMARY["anomalies"], [chunk()])
    prompt = client.messages.last_request["messages"][0]["content"]
    assert "final and authoritative" in prompt
    assert "Net VAT: 14550.00 — position: payable" in prompt
    assert "TXN-5003" in prompt  # excluded transaction surfaced


def test_refusal_returns_none_narrative():
    out = draft_filing_support_narrative(refusal_client(category="cyber"), SUMMARY, BY_TREATMENT, [], [chunk()])
    assert out.narrative is None
    assert out.refused is True
    assert out.refusal_category == "cyber"
    assert out.prompt_hash


def test_missing_tool_call_is_a_parse_error():
    out = draft_filing_support_narrative(no_tool_call_client(), SUMMARY, BY_TREATMENT, [], [chunk()])
    assert out.narrative is None
    assert out.refused is False
    assert "no record_filing_support_narrative tool call" in out.parse_error


def test_empty_position_summary_is_a_parse_error():
    client = narrative_client({
        "position_summary": "  ", "anomaly_explanations": [],
        "specialist_review_needed": False, "citations": [],
    })
    out = draft_filing_support_narrative(client, SUMMARY, BY_TREATMENT, [], [chunk()])
    assert out.narrative is None
    assert "empty position_summary" in out.parse_error


def test_non_bool_specialist_flag_is_a_parse_error():
    client = narrative_client({
        "position_summary": "ok", "anomaly_explanations": [],
        "specialist_review_needed": "yes", "citations": [],
    })
    out = draft_filing_support_narrative(client, SUMMARY, BY_TREATMENT, [], [chunk()])
    assert out.narrative is None
    assert "specialist_review_needed must be a boolean" in out.parse_error


def test_non_list_field_is_a_parse_error():
    client = narrative_client({
        "position_summary": "ok", "anomaly_explanations": "not a list",
        "specialist_review_needed": False, "citations": [],
    })
    out = draft_filing_support_narrative(client, SUMMARY, BY_TREATMENT, [], [chunk()])
    assert out.narrative is None
    assert "anomaly_explanations must be a list" in out.parse_error
