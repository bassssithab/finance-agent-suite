from decimal import Decimal

from knowledge import Chunk, SearchResult

from ar_collections_agent.draft import RECORD_DUNNING_DRAFTS_TOOL, draft_dunning_emails
from ar_collections_agent.models import InvoiceAging
from fakes import dunning_client, no_tool_call_client, refusal_client
from fixtures import POLICY_CITATION, dunning_payload

TOOL_NAME = "record_dunning_drafts"


def aging(invoice_id="INV-1", tone_tier="reminder", days_overdue=45, amount="1000.00"):
    return InvoiceAging(
        invoice_id=invoice_id,
        customer="Acme",
        invoice_date="2026-07-01",
        due_date="2026-07-18",
        amount=Decimal(amount),
        currency="USD",
        last_payment_date=None,
        days_overdue=days_overdue,
        days_since_last_payment=None,
        bucket="31-60",
        flagged=True,
        flag_reasons=[f"{days_overdue} days overdue >= threshold 31"],
        tone_tier=tone_tier,
    )


def chunk(doc_id="doc-x", position=0, text="Terms are net 30; first reminder is courteous."):
    c = Chunk(
        chunk_id=f"{doc_id}:{position}",
        doc_id=doc_id,
        doc_title="Collections Policy",
        corpus="collections_policy",
        position=position,
        text=text,
    )
    return SearchResult(chunk=c, score=1.0)


def test_tool_schema_requires_one_draft_per_field():
    props = RECORD_DUNNING_DRAFTS_TOOL["input_schema"]["properties"]["drafts"]["items"]
    assert set(props["required"]) == {"invoice_id", "tone", "subject", "body", "citations"}


def test_grounded_drafts_are_parsed_in_flagged_order():
    flagged = [aging("INV-1", "reminder"), aging("INV-2", "firm", days_overdue=75)]
    client = dunning_client(dunning_payload([("INV-1", "reminder"), ("INV-2", "firm")]))

    out = draft_dunning_emails(client, flagged, [chunk()])

    assert [d.invoice_id for d in out.drafts] == ["INV-1", "INV-2"]
    assert [d.tone for d in out.drafts] == ["reminder", "firm"]
    assert all(d.citations == [POLICY_CITATION] for d in out.drafts)
    assert out.refused is False and out.parse_error is None

    req = client.messages.last_request
    assert req["model"] == "claude-sonnet-5"
    assert req["tool_choice"] == {"type": "tool", "name": TOOL_NAME}
    assert req["output_config"] == {"effort": "medium"}


def test_prompt_carries_the_assigned_tone_tier_and_authoritative_figures():
    flagged = [aging("INV-1", "formal", days_overdue=120, amount="42000.00")]
    client = dunning_client(dunning_payload([("INV-1", "formal")]))

    draft_dunning_emails(client, flagged, [chunk()])

    prompt = client.messages.last_request["messages"][0]["content"]
    assert "write in tone tier: formal" in prompt
    assert "120 days overdue" in prompt
    assert "final and authoritative" in prompt


def test_tone_is_taken_from_our_aging_not_the_models_echo():
    flagged = [aging("INV-1", "formal", days_overdue=120)]
    # model wrongly echoes "reminder"
    payload = dunning_payload([("INV-1", "reminder")])
    out = draft_dunning_emails(dunning_client(payload), flagged, [chunk()])
    assert out.drafts[0].tone == "formal"


def test_ungrounded_run_passes_no_chunks_and_expects_empty_citations():
    flagged = [aging("INV-1", "reminder")]
    client = dunning_client(dunning_payload([("INV-1", "reminder")], citation=None))

    out = draft_dunning_emails(client, flagged, [])

    assert out.chunk_ids == []
    assert out.citations == []
    assert out.drafts[0].citations == []
    prompt = client.messages.last_request["messages"][0]["content"]
    assert "<context>\n(no collections-policy excerpts" in prompt


def test_refusal_returns_none_drafts():
    out = draft_dunning_emails(refusal_client(category="cyber"), [aging()], [chunk()])
    assert out.drafts is None
    assert out.refused is True
    assert out.refusal_category == "cyber"
    assert out.prompt_hash


def test_missing_tool_call_is_a_parse_error():
    out = draft_dunning_emails(no_tool_call_client(), [aging()], [chunk()])
    assert out.drafts is None
    assert out.refused is False
    assert "no record_dunning_drafts tool call" in out.parse_error


def test_unknown_invoice_id_from_model_is_a_parse_error():
    flagged = [aging("INV-1", "reminder")]
    client = dunning_client(dunning_payload([("INV-9", "reminder")]))
    out = draft_dunning_emails(client, flagged, [chunk()])
    assert out.drafts is None
    assert "unknown invoice" in out.parse_error


def test_invoice_the_model_omitted_is_filled_with_a_placeholder():
    flagged = [aging("INV-1", "reminder"), aging("INV-2", "firm", days_overdue=75)]
    client = dunning_client(dunning_payload([("INV-1", "reminder")]))

    out = draft_dunning_emails(client, flagged, [chunk()])

    assert [d.invoice_id for d in out.drafts] == ["INV-1", "INV-2"]
    assert out.drafts[1].body == "No draft returned for this invoice."
    assert out.drafts[1].tone == "firm"
    assert out.drafts[1].citations == []
