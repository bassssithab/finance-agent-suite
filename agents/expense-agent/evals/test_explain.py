from decimal import Decimal

from knowledge import Chunk, SearchResult

from expense_agent.explain import (
    RECORD_POLICY_EXPLANATIONS_TOOL,
    draft_policy_explanations,
)
from expense_agent.models import ExtractedReceipt, Violation
from fakes import explanation_client, no_tool_call_client, refusal_client
from fixtures import POLICY_CITATION, explanations_payload

TOOL_NAME = "record_policy_explanations"


def rcpt(category="Meals", amount="182.50"):
    return ExtractedReceipt(
        vendor="The Copper Table",
        date="2026-08-25",
        amount=Decimal(amount),
        currency="USD",
        expense_category=category,
        extraction_confidence=0.94,
    )


def violation(code="category_over_limit", field="amount"):
    return Violation(code=code, field=field, detail=f"{code}: deterministic reason")


def chunk(doc_id="doc-x", position=0, text="Meals are reimbursed up to 75 dollars per person."):
    c = Chunk(
        chunk_id=f"{doc_id}:{position}",
        doc_id=doc_id,
        doc_title="Expense Policy",
        corpus="expense_policy",
        position=position,
        text=text,
    )
    return SearchResult(chunk=c, score=1.0)


def test_tool_schema_requires_one_explanation_per_field():
    props = RECORD_POLICY_EXPLANATIONS_TOOL["input_schema"]["properties"]["explanations"]["items"]
    assert set(props["required"]) == {"code", "explanation", "citations"}


def test_grounded_explanations_are_parsed_in_violation_order():
    violations = [violation("category_over_limit"), violation("receipt_too_old", "date")]
    client = explanation_client(
        explanations_payload(["category_over_limit", "receipt_too_old"])
    )

    out = draft_policy_explanations(client, rcpt(), violations, [chunk()])

    assert [e.code for e in out.explanations] == ["category_over_limit", "receipt_too_old"]
    assert all(e.citations == [POLICY_CITATION] for e in out.explanations)
    assert out.refused is False and out.parse_error is None

    req = client.messages.last_request
    assert req["model"] == "claude-sonnet-5"
    assert req["tool_choice"] == {"type": "tool", "name": TOOL_NAME}
    assert req["output_config"] == {"effort": "medium"}


def test_ungrounded_run_passes_no_chunks_and_expects_empty_citations():
    violations = [violation("receipt_too_old", "date")]
    client = explanation_client(explanations_payload(["receipt_too_old"], citation=None))

    out = draft_policy_explanations(client, rcpt(), violations, [])

    assert out.chunk_ids == []
    assert out.citations == []
    assert out.explanations[0].citations == []
    prompt = client.messages.last_request["messages"][0]["content"]
    assert "<context>\n\n</context>" in prompt or "<context>\n</context>" in prompt


def test_refusal_returns_none_explanations():
    out = draft_policy_explanations(refusal_client(category="cyber"), rcpt(), [violation()], [chunk()])
    assert out.explanations is None
    assert out.refused is True
    assert out.refusal_category == "cyber"
    assert out.prompt_hash


def test_missing_tool_call_is_a_parse_error():
    out = draft_policy_explanations(no_tool_call_client(), rcpt(), [violation()], [chunk()])
    assert out.explanations is None
    assert out.refused is False
    assert "no record_policy_explanations tool call" in out.parse_error


def test_unknown_violation_code_from_model_is_a_parse_error():
    client = explanation_client(explanations_payload(["missing_required_field"]))
    out = draft_policy_explanations(client, rcpt(), [violation("category_over_limit")], [chunk()])
    assert out.explanations is None
    assert "unknown violation code" in out.parse_error


def test_violation_the_model_omitted_is_filled_with_a_placeholder():
    violations = [violation("category_over_limit"), violation("receipt_too_old", "date")]
    client = explanation_client(explanations_payload(["category_over_limit"]))

    out = draft_policy_explanations(client, rcpt(), violations, [chunk()])

    assert [e.code for e in out.explanations] == ["category_over_limit", "receipt_too_old"]
    assert out.explanations[1].explanation == "No explanation returned for this violation."
    assert out.explanations[1].citations == []
