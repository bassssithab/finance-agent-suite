import pytest
from knowledge import KnowledgeBase

from ap_agent.extraction import extract_invoice
from ap_agent.coding import suggest_coding
from ap_agent.runner import _retrieve_coding_context
from fakes import invoice_client, refusal_client
from fixtures import (
    ALL_DOCUMENTS,
    CODING_PAYLOADS,
    COA_CITATION,
    CHART_OF_ACCOUNTS,
    record_invoice_payload,
)

IMAGE = b"img"


@pytest.fixture
def knowledge_base():
    kb = KnowledgeBase()
    kb.ingest(ALL_DOCUMENTS)
    return kb


def _invoice(slug):
    client = invoice_client(record_invoice_payload(slug))
    return extract_invoice(client, content=IMAGE, media_type="image/png").invoice


def test_coa_citation_constant_matches_the_ingested_chunk(knowledge_base):
    # If platform/knowledge's chunking changes and the chart of accounts no
    # longer lands in a single chunk 0, this fails loudly here instead of
    # silently breaking the canned coding payloads.
    results = knowledge_base.search("office supplies freight advisory fees", top_k=5)
    assert any(r.chunk.citation == COA_CITATION for r in results)


def test_retrieve_coding_context_dedups_across_line_items(knowledge_base):
    invoice = _invoice("clean_office_supplies")

    chunks = _retrieve_coding_context(knowledge_base, invoice, top_k=3)

    # 3 line items all hit the same single COA chunk -> one unique chunk.
    assert len(chunks) == 1
    assert chunks[0].chunk.citation == COA_CITATION


def test_retrieve_coding_context_empty_when_nothing_ingested():
    invoice = _invoice("clean_office_supplies")
    assert _retrieve_coding_context(KnowledgeBase(), invoice, top_k=3) == []


def test_suggests_one_account_per_line_with_citations(knowledge_base):
    invoice = _invoice("consulting_services")
    chunks = _retrieve_coding_context(knowledge_base, invoice, top_k=3)
    client = invoice_client(
        record_invoice_payload("consulting_services"),
        coding_payload=CODING_PAYLOADS["consulting_services"],
    )

    result = suggest_coding(client, invoice, chunks)

    assert result.refused is False and result.parse_error is None
    assert [s.account_code for s in result.suggestions] == ["6200", "6200"]
    assert all(s.citation == COA_CITATION for s in result.suggestions)
    assert result.chunk_ids == [c.chunk.chunk_id for c in chunks]

    request = client.messages.last_request
    assert request["tool_choice"] == {"type": "tool", "name": "record_gl_coding"}
    assert CHART_OF_ACCOUNTS.title in request["messages"][0]["content"]


def test_missing_line_gets_a_null_placeholder_suggestion(knowledge_base):
    invoice = _invoice("consulting_services")
    chunks = _retrieve_coding_context(knowledge_base, invoice, top_k=3)
    # Model only returned a suggestion for line 0.
    partial = {"suggestions": [CODING_PAYLOADS["consulting_services"]["suggestions"][0]]}
    client = invoice_client(record_invoice_payload("consulting_services"), coding_payload=partial)

    result = suggest_coding(client, invoice, chunks)

    assert len(result.suggestions) == 2
    assert result.suggestions[1].account_code is None
    assert result.suggestions[1].citation is None


def test_refusal_returns_no_suggestions_but_keeps_bookkeeping(knowledge_base):
    invoice = _invoice("clean_office_supplies")
    chunks = _retrieve_coding_context(knowledge_base, invoice, top_k=3)

    result = suggest_coding(refusal_client("cyber"), invoice, chunks)

    assert result.suggestions is None
    assert result.refused is True
    assert result.chunk_ids == [c.chunk.chunk_id for c in chunks]
