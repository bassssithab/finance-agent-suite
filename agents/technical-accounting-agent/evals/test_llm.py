from fakes import refusal_response, text_response
from fixtures import SOFTWARE_CAPITALIZATION_STANDARD
from knowledge import KnowledgeBase
from technical_accounting_agent.llm import DEFAULT_EFFORT, DEFAULT_MODEL, build_user_prompt, draft_answer


def _software_capitalization_chunks(query="application development stage capitalize payroll"):
    kb = KnowledgeBase()
    kb.ingest([SOFTWARE_CAPITALIZATION_STANDARD])
    results = kb.search(query)
    assert results, "fixture query must actually return chunks for these tests to mean anything"
    return results


def test_user_prompt_embeds_each_chunk_citation_and_text():
    chunks = _software_capitalization_chunks()

    prompt = build_user_prompt("When can payroll costs be capitalized?", chunks)

    for result in chunks:
        assert result.chunk.citation in prompt
        assert result.chunk.text in prompt
    assert "Question: When can payroll costs be capitalized?" in prompt


def test_draft_answer_sends_model_effort_system_and_user_prompt():
    chunks = _software_capitalization_chunks()
    client = text_response("Payroll costs are capitalized during the application development stage.")

    draft_answer(client, "When can payroll costs be capitalized?", chunks, model="claude-sonnet-5", effort="medium")

    request = client.messages.last_request
    assert request["model"] == "claude-sonnet-5"
    assert request["output_config"] == {"effort": "medium"}
    assert "ONLY the excerpts provided" in request["system"]
    assert request["messages"] == [
        {"role": "user", "content": build_user_prompt("When can payroll costs be capitalized?", chunks)}
    ]


def test_draft_answer_parses_a_normal_text_response():
    chunks = _software_capitalization_chunks()
    client = text_response("Payroll costs are capitalized during the application development stage.")

    draft = draft_answer(client, "When can payroll costs be capitalized?", chunks)

    assert draft.refused is False
    assert draft.answer_text == "Payroll costs are capitalized during the application development stage."
    assert draft.citations == [r.chunk.citation for r in chunks]
    assert draft.chunk_ids == [r.chunk.chunk_id for r in chunks]
    assert draft.model == DEFAULT_MODEL
    assert draft.refusal_category is None


def test_draft_answer_handles_a_refusal_without_raising():
    chunks = _software_capitalization_chunks()
    client = refusal_response(category="cyber")

    draft = draft_answer(client, "irrelevant question", chunks)

    assert draft.refused is True
    assert draft.refusal_category == "cyber"
    assert draft.answer_text is None
    # Even on refusal, citation/chunk bookkeeping is preserved for the audit trail.
    assert draft.chunk_ids == [r.chunk.chunk_id for r in chunks]


def test_prompt_hash_is_stable_for_the_same_question_and_chunks_but_differs_otherwise():
    chunks = _software_capitalization_chunks()
    client = text_response("answer")

    first = draft_answer(client, "same question", chunks)
    second = draft_answer(client, "same question", chunks)
    third = draft_answer(client, "different question", chunks)

    assert first.prompt_hash == second.prompt_hash
    assert first.prompt_hash != third.prompt_hash


def test_default_model_and_effort_are_sonnet_5_medium():
    # Locks in the deliberate cost tradeoff from the plan: Sonnet 5 / medium
    # effort while this agent is new and being debugged (see README).
    assert DEFAULT_MODEL == "claude-sonnet-5"
    assert DEFAULT_EFFORT == "medium"
