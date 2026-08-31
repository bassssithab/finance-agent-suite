from decimal import Decimal

from connectors import BudgetActualLine
from knowledge import KnowledgeBase

from close_agent import FlagThresholds, compute_variances
from close_agent.explain import (
    DEFAULT_EFFORT,
    DEFAULT_MODEL,
    build_user_prompt,
    describe_variance,
    draft_explanations,
)
from fakes import explanations_client, no_tool_call_client, refusal_client
from fixtures import ALL_DOCUMENTS, POLICY_CITATION, explanations_payload


def _line(capability, account, line_item, amount):
    return BudgetActualLine(
        source_system="test_co", source_capability=capability, period="2026-07",
        account=account, line_item=line_item, category="Operating expenses",
        amount=Decimal(amount), currency="USD", raw={},
    )


def _flagged(pairs):
    """pairs: list of (account, line_item, budget, actual)."""
    budget = [_line("budget", a, li, b) for (a, li, b, _act) in pairs]
    actuals = [_line("actuals", a, li, act) for (a, li, _b, act) in pairs]
    variances = compute_variances(
        budget, actuals, period="2026-07", currency="USD", thresholds=FlagThresholds(),
    )
    return [lv for lv in variances if lv.flagged]


MARKETING = ("6000", "Marketing — paid media", "40000.00", "62000.00")


def _chunks_for(flagged):
    kb = KnowledgeBase()
    kb.ingest(ALL_DOCUMENTS)
    seen, chunks = set(), []
    for lv in flagged:
        for result in kb.search(describe_variance(lv)):
            if result.chunk.chunk_id not in seen:
                seen.add(result.chunk.chunk_id)
                chunks.append(result)
    assert chunks, "fixture query must return chunks for these tests to mean anything"
    return chunks


def test_describe_variance_mentions_account_and_figures():
    [lv] = _flagged([MARKETING])
    text = describe_variance(lv)
    assert "account 6000 Marketing — paid media" in text
    assert "62000.00" in text and "22000.00" in text


def test_user_prompt_embeds_each_chunk_and_the_figures():
    flagged = _flagged([MARKETING])
    chunks = _chunks_for(flagged)

    prompt = build_user_prompt(flagged, chunks)

    for result in chunks:
        assert result.chunk.citation in prompt
        assert result.chunk.text in prompt
    assert "budget 40000.00 USD, actual 62000.00 USD, variance 22000.00" in prompt
    assert "flagged because:" in prompt


def test_user_prompt_notes_absence_of_context_when_ungrounded():
    flagged = _flagged([MARKETING])
    prompt = build_user_prompt(flagged, [])
    assert "no accounting-policy excerpts were retrieved" in prompt


def test_draft_explanations_sends_model_effort_system_and_tool():
    flagged = _flagged([MARKETING])
    chunks = _chunks_for(flagged)
    client = explanations_client(explanations_payload([("6000", "Marketing — paid media")]))

    draft_explanations(client, flagged, chunks, model="claude-sonnet-5", effort="medium")

    request = client.messages.last_request
    assert request["model"] == "claude-sonnet-5"
    assert request["output_config"] == {"effort": "medium"}
    assert "authoritative" in request["system"]
    assert request["tools"][0]["name"] == "record_variance_explanations"
    assert request["tool_choice"] == {"type": "tool", "name": "record_variance_explanations"}
    assert request["messages"] == [
        {"role": "user", "content": build_user_prompt(flagged, chunks)}
    ]


def test_draft_explanations_parses_tool_output():
    flagged = _flagged([MARKETING])
    chunks = _chunks_for(flagged)
    client = explanations_client(explanations_payload([("6000", "Marketing — paid media")]))

    result = draft_explanations(client, flagged, chunks)

    assert result.refused is False
    assert result.parse_error is None
    assert [e.account for e in result.explanations] == ["6000"]
    assert result.explanations[0].citations == [POLICY_CITATION]
    assert result.model == DEFAULT_MODEL


def test_draft_explanations_fills_a_line_the_model_omitted():
    flagged = _flagged([
        MARKETING,
        ("6200", "Contract labour", "30000.00", "12000.00"),
    ])
    chunks = _chunks_for(flagged)
    # payload only covers the first flagged line
    client = explanations_client(explanations_payload([("6000", "Marketing — paid media")]))

    result = draft_explanations(client, flagged, chunks)

    assert [e.account for e in result.explanations] == ["6000", "6200"]
    assert result.explanations[1].explanation == "No explanation returned for this line."
    assert result.explanations[1].citations == []


def test_explanation_for_unknown_line_is_a_parse_error():
    flagged = _flagged([MARKETING])
    chunks = _chunks_for(flagged)
    client = explanations_client(explanations_payload([("9999", "Not a real line")]))

    result = draft_explanations(client, flagged, chunks)

    assert result.explanations is None
    assert "unknown line" in result.parse_error


def test_refusal_is_handled_without_raising():
    flagged = _flagged([MARKETING])
    chunks = _chunks_for(flagged)

    result = draft_explanations(refusal_client(category="cyber"), flagged, chunks)

    assert result.refused is True
    assert result.refusal_category == "cyber"
    assert result.explanations is None
    assert result.chunk_ids == [r.chunk.chunk_id for r in chunks]


def test_missing_tool_call_is_a_parse_error():
    flagged = _flagged([MARKETING])
    chunks = _chunks_for(flagged)

    result = draft_explanations(no_tool_call_client(), flagged, chunks)

    assert result.explanations is None
    assert "no record_variance_explanations tool call" in result.parse_error


def test_prompt_hash_is_stable_and_varies_with_input():
    flagged = _flagged([MARKETING])
    other = _flagged([("6200", "Contract labour", "30000.00", "12000.00")])
    chunks = _chunks_for(flagged)
    client = explanations_client(explanations_payload([("6000", "Marketing — paid media")]))

    first = draft_explanations(client, flagged, chunks)
    second = draft_explanations(client, flagged, chunks)
    third = draft_explanations(client, other, chunks)

    assert first.prompt_hash == second.prompt_hash
    assert first.prompt_hash != third.prompt_hash


def test_default_model_and_effort_are_sonnet_5_medium():
    assert DEFAULT_MODEL == "claude-sonnet-5"
    assert DEFAULT_EFFORT == "medium"
