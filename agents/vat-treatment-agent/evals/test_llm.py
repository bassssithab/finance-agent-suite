from fakes import refusal_response, text_response
from fixtures import OUT_OF_SCOPE_DROP_SHIPMENT
from knowledge import KnowledgeBase
from vat_treatment_agent.llm import DEFAULT_EFFORT, DEFAULT_MODEL, build_user_prompt, draft_treatment
from vat_treatment_agent.models import InvoiceLineItem, describe_line_item

LINE_ITEM = InvoiceLineItem(
    goods_type="consumer electronics",
    customer_location="a country other than Larenthia",
    transaction_type="drop-shipped directly from a foreign supplier to the foreign customer",
)


def _drop_shipment_chunks(query=None):
    kb = KnowledgeBase()
    kb.ingest([OUT_OF_SCOPE_DROP_SHIPMENT])
    results = kb.search(query or describe_line_item(LINE_ITEM))
    assert results, "fixture query must actually return chunks for these tests to mean anything"
    return results


def test_user_prompt_embeds_each_chunk_citation_and_text():
    chunks = _drop_shipment_chunks()

    prompt = build_user_prompt(LINE_ITEM, chunks)

    for result in chunks:
        assert result.chunk.citation in prompt
        assert result.chunk.text in prompt
    assert f"Question: {describe_line_item(LINE_ITEM)}" in prompt


def test_draft_treatment_sends_model_effort_system_and_user_prompt():
    chunks = _drop_shipment_chunks()
    client = text_response("This supply is out-of-scope.")

    draft_treatment(client, LINE_ITEM, chunks, model="claude-sonnet-5", effort="medium")

    request = client.messages.last_request
    assert request["model"] == "claude-sonnet-5"
    assert request["output_config"] == {"effort": "medium"}
    assert "ONLY the excerpts provided" in request["system"]
    assert request["messages"] == [
        {"role": "user", "content": build_user_prompt(LINE_ITEM, chunks)}
    ]


def test_draft_treatment_parses_a_normal_text_response():
    chunks = _drop_shipment_chunks()
    client = text_response("This supply is out-of-scope because the goods never enter Larenthia.")

    draft = draft_treatment(client, LINE_ITEM, chunks)

    assert draft.refused is False
    assert draft.answer_text == "This supply is out-of-scope because the goods never enter Larenthia."
    assert draft.citations == [r.chunk.citation for r in chunks]
    assert draft.chunk_ids == [r.chunk.chunk_id for r in chunks]
    assert draft.model == DEFAULT_MODEL
    assert draft.refusal_category is None


def test_draft_treatment_handles_a_refusal_without_raising():
    chunks = _drop_shipment_chunks()
    client = refusal_response(category="cyber")

    draft = draft_treatment(client, LINE_ITEM, chunks)

    assert draft.refused is True
    assert draft.refusal_category == "cyber"
    assert draft.answer_text is None
    # Even on refusal, citation/chunk bookkeeping is preserved for the audit trail.
    assert draft.chunk_ids == [r.chunk.chunk_id for r in chunks]


def test_prompt_hash_is_stable_for_the_same_line_item_and_chunks_but_differs_otherwise():
    chunks = _drop_shipment_chunks()
    client = text_response("answer")
    other_item = InvoiceLineItem(
        goods_type="furniture", customer_location="Larenthia", transaction_type="domestic sale"
    )

    first = draft_treatment(client, LINE_ITEM, chunks)
    second = draft_treatment(client, LINE_ITEM, chunks)
    third = draft_treatment(client, other_item, chunks)

    assert first.prompt_hash == second.prompt_hash
    assert first.prompt_hash != third.prompt_hash


def test_default_model_and_effort_are_sonnet_5_medium():
    # Locks in the deliberate cost tradeoff from the plan: Sonnet 5 / medium
    # effort while this agent is new and being debugged (see README).
    assert DEFAULT_MODEL == "claude-sonnet-5"
    assert DEFAULT_EFFORT == "medium"
