from knowledge import Chunk, SearchResult

from fpa_agent.narrate import (
    RECORD_FORECAST_NARRATIVE_TOOL,
    SYSTEM_PROMPT,
    draft_forecast_narrative,
)
from fakes import narrative_client, no_tool_call_client, refusal_client
from fixtures import METHODOLOGY_CITATION, narrative_payload

TOOL_NAME = "record_forecast_narrative"

SUMMARY = {
    "line_count": 3,
    "base_period": "2026-06",
    "projected_periods": ["2026-07", "2026-08", "2026-09"],
    "total_base": "1000000.00",
    "total_projected_final": "1100000.00",
    "total_growth_pct_over_horizon": "0.1000",
    "by_category": {
        "Revenue": {"base": "900000.00", "final": "1000000.00", "growth_pct_over_horizon": "0.1111"},
        "Operating expenses": {"base": "100000.00", "final": "100000.00", "growth_pct_over_horizon": "0.0000"},
    },
    "flagged_lines": [],
}
ASSUMPTIONS = {
    "default_growth": "0.02",
    "category_growth": {"Revenue": "0.30"},
    "max_pop_change_pct": "0.25",
    "flag_negative": True,
}
FLAGGED = [
    {
        "account": "4000", "line_item": "Product revenue", "category": "Revenue",
        "period": "2026-07", "growth_rate": "0.30", "growth_source": "category",
        "projected_amount": "1170000.00",
        "flag_reasons": ["assumed growth rate 30.0% per period >= sensitivity threshold 25.0%"],
    }
]


def chunk(doc_id="doc-x", position=0, text="A driver-based forecast projects each line forward."):
    c = Chunk(
        chunk_id=f"{doc_id}:{position}",
        doc_id=doc_id,
        doc_title="FP&A Methodology",
        corpus="fpa_methodology",
        position=position,
        text=text,
    )
    return SearchResult(chunk=c, score=1.0)


def test_tool_schema_requires_all_four_fields():
    props = RECORD_FORECAST_NARRATIVE_TOOL["input_schema"]
    assert set(props["required"]) == {
        "summary", "assumptions_described", "flagged_items_called_out", "citations"
    }


def test_system_prompt_forbids_certainty_and_requires_assumptions_framing():
    p = SYSTEM_PROMPT.lower()
    assert "not a prediction, a guarantee" in p
    assert "never write that a projected figure \"will\" be reached" in p
    assert "do not attach confidence levels" in p
    assert "as an assumption" in p
    assert "never as an established fact" in p
    # must not endorse / conclude reasonableness
    assert "do not recommend a decision" in p
    assert "reasonable, achievable, conservative, or aggressive" in p


def test_grounded_narrative_is_parsed():
    client = narrative_client(narrative_payload(flagged_line_items=["4000 Product revenue"]))
    out = draft_forecast_narrative(client, SUMMARY, ASSUMPTIONS, FLAGGED, [chunk()])

    assert out.narrative is not None
    assert out.narrative.summary
    assert out.narrative.assumptions_described
    assert out.narrative.flagged_items_called_out
    assert out.narrative.citations == [METHODOLOGY_CITATION]
    assert out.refused is False and out.parse_error is None

    req = client.messages.last_request
    assert req["model"] == "claude-sonnet-5"
    assert req["tool_choice"] == {"type": "tool", "name": TOOL_NAME}
    assert req["output_config"] == {"effort": "medium"}


def test_ungrounded_run_passes_no_chunks_and_expects_empty_citations():
    client = narrative_client(narrative_payload(citation=None))
    out = draft_forecast_narrative(client, SUMMARY, ASSUMPTIONS, [], [])

    assert out.chunk_ids == []
    assert out.citations == []
    assert out.narrative.citations == []
    prompt = client.messages.last_request["messages"][0]["content"]
    assert "(no FP&A-methodology excerpts were retrieved" in prompt


def test_user_prompt_states_figures_are_authoritative_and_lists_assumptions():
    client = narrative_client(narrative_payload())
    draft_forecast_narrative(client, SUMMARY, ASSUMPTIONS, FLAGGED, [chunk()])
    prompt = client.messages.last_request["messages"][0]["content"]
    assert "final and authoritative" in prompt
    assert "Revenue: 0.30 per period (category assumption)" in prompt
    assert "each supplied by the planner" in prompt


def test_refusal_returns_none_narrative():
    out = draft_forecast_narrative(refusal_client(category="cyber"), SUMMARY, ASSUMPTIONS, [], [chunk()])
    assert out.narrative is None
    assert out.refused is True
    assert out.refusal_category == "cyber"
    assert out.prompt_hash


def test_missing_tool_call_is_a_parse_error():
    out = draft_forecast_narrative(no_tool_call_client(), SUMMARY, ASSUMPTIONS, [], [chunk()])
    assert out.narrative is None
    assert out.refused is False
    assert "no record_forecast_narrative tool call" in out.parse_error


def test_empty_summary_is_a_parse_error():
    client = narrative_client({"summary": "  ", "assumptions_described": [], "flagged_items_called_out": [], "citations": []})
    out = draft_forecast_narrative(client, SUMMARY, ASSUMPTIONS, [], [chunk()])
    assert out.narrative is None
    assert "empty summary" in out.parse_error


def test_non_list_field_is_a_parse_error():
    client = narrative_client({
        "summary": "ok", "assumptions_described": "not a list",
        "flagged_items_called_out": [], "citations": [],
    })
    out = draft_forecast_narrative(client, SUMMARY, ASSUMPTIONS, [], [chunk()])
    assert out.narrative is None
    assert "assumptions_described must be a list" in out.parse_error
