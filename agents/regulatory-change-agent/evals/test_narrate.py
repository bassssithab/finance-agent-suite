from knowledge import Chunk, SearchResult

from regulatory_change_agent.models import ControlRelevance
from regulatory_change_agent.narrate import (
    RECORD_IMPACT_ASSESSMENT_TOOL,
    SYSTEM_PROMPT,
    draft_impact_assessment,
)
from fakes import assessment_client, no_tool_call_client, refusal_client
from fixtures import PROCEDURE_CITATION, assessment_payload

TOOL_NAME = "record_impact_assessment"

POLICY = {"min_keyword_overlap": 2, "strong_overlap": 4, "max_controls_surfaced": 8, "extra_stopwords": []}


def cr(control_id="CTL-101", score=9, terms=("access", "authentication")):
    return ControlRelevance(
        control_id=control_id,
        description="Privileged system access requires MFA",
        category="Access Management",
        score=score,
        matched_terms=list(terms),
        category_match=True,
        relevant=True,
    )


def chunk(doc_id="doc-x", position=0, text="A first-pass triage is a keyword scan only, not a compliance assessment."):
    c = Chunk(
        chunk_id=f"{doc_id}:{position}",
        doc_id=doc_id,
        doc_title="Regulatory Change Management Procedure",
        corpus="regulatory_guidance",
        position=position,
        text=text,
    )
    return SearchResult(chunk=c, score=1.0)


def _draft(client, *, surfaced=None, verdict="apparent_coverage", gap_flagged=False, flag_reasons=None, chunks=None):
    return draft_impact_assessment(
        client,
        requirement_text="Privileged access must require MFA.",
        requirement_reference="LT-REG-2026-014",
        policy=POLICY,
        surfaced=surfaced if surfaced is not None else [cr()],
        coverage_verdict=verdict,
        gap_flagged=gap_flagged,
        flag_reasons=flag_reasons or [],
        control_count=10,
        chunks=chunks if chunks is not None else [chunk()],
    )


def test_tool_schema_requires_all_five_fields():
    assert set(RECORD_IMPACT_ASSESSMENT_TOOL["input_schema"]["required"]) == {
        "assessment", "relevant_controls_explained", "gap_explanation",
        "review_required_statement", "citations",
    }


def test_system_prompt_forbids_compliance_conclusions_and_demands_review():
    p = SYSTEM_PROMPT.lower()
    assert "first-pass" in p
    assert "not a legal or compliance determination" in p
    assert '"no action needed"' in p
    assert '"there is a gap"' in p
    assert '"the requirement is satisfied"' in p
    assert "qualified legal" in p
    assert "regardless of what the triage found" in p
    assert "not the same as actually addressing the requirement" in p


def test_grounded_narrative_is_parsed():
    client = assessment_client(assessment_payload(control_ids=["CTL-101"]))
    out = _draft(client)

    assert out.narrative is not None
    assert out.narrative.assessment
    assert out.narrative.review_required_statement
    assert out.narrative.relevant_controls_explained[0]["control_id"] == "CTL-101"
    assert out.narrative.gap_explanation is None
    assert out.narrative.citations == [PROCEDURE_CITATION]
    assert out.refused is False and out.parse_error is None

    req = client.messages.last_request
    assert req["model"] == "claude-sonnet-5"
    assert req["tool_choice"] == {"type": "tool", "name": TOOL_NAME}
    assert req["output_config"] == {"effort": "medium"}


def test_gap_explanation_is_carried_when_present():
    client = assessment_client(assessment_payload(gap=True, control_ids=[]))
    out = _draft(client, surfaced=[], verdict="likely_gap", gap_flagged=True,
                 flag_reasons=["no existing control shares at least 2 key terms"])
    assert out.narrative.gap_explanation is not None
    assert out.narrative.relevant_controls_explained == []


def test_ungrounded_run_passes_no_chunks_and_expects_empty_citations():
    client = assessment_client(assessment_payload(control_ids=["CTL-101"], citation=None))
    out = _draft(client, chunks=[])

    assert out.chunk_ids == []
    assert out.citations == []
    assert out.narrative.citations == []
    prompt = client.messages.last_request["messages"][0]["content"]
    assert "(no regulatory-guidance excerpts were retrieved" in prompt


def test_user_prompt_states_the_shortlist_is_not_confirmed():
    client = assessment_client(assessment_payload(control_ids=["CTL-101"]))
    _draft(client)
    prompt = client.messages.last_request["messages"][0]["content"]
    assert "NOT confirmed to address the requirement" in prompt
    assert "Coverage verdict: apparent_coverage" in prompt


def test_refusal_returns_none_narrative():
    out = _draft(refusal_client(category="cyber"))
    assert out.narrative is None
    assert out.refused is True
    assert out.refusal_category == "cyber"
    assert out.prompt_hash


def test_missing_tool_call_is_a_parse_error():
    out = _draft(no_tool_call_client())
    assert out.narrative is None
    assert out.refused is False
    assert "no record_impact_assessment tool call" in out.parse_error


def test_empty_assessment_is_a_parse_error():
    payload = assessment_payload(control_ids=["CTL-101"]) | {"assessment": "  "}
    out = _draft(assessment_client(payload))
    assert out.narrative is None
    assert "empty assessment" in out.parse_error


def test_empty_review_statement_is_a_parse_error():
    payload = assessment_payload(control_ids=["CTL-101"]) | {"review_required_statement": ""}
    out = _draft(assessment_client(payload))
    assert out.narrative is None
    assert "empty review_required_statement" in out.parse_error


def test_non_list_controls_explained_is_a_parse_error():
    payload = assessment_payload(control_ids=["CTL-101"]) | {"relevant_controls_explained": "nope"}
    out = _draft(assessment_client(payload))
    assert out.narrative is None
    assert "relevant_controls_explained must be a list" in out.parse_error
