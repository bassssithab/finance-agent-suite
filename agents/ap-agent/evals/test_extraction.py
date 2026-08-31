import base64
from decimal import Decimal

from ap_agent.extraction import (
    DEFAULT_EFFORT,
    DEFAULT_MODEL,
    extract_invoice,
)
from fakes import invoice_client, no_tool_call_client, refusal_client
from fixtures import record_invoice_payload

IMAGE_BYTES = b"\x89PNG\r\n\x1a\n-pretend-this-is-an-invoice"


def _run(payload=None, client=None, **kwargs):
    payload = payload or record_invoice_payload("clean_office_supplies")
    client = client or invoice_client(payload)
    result = extract_invoice(
        client, content=IMAGE_BYTES, media_type="image/png", **kwargs
    )
    return client, result


def test_sends_image_block_as_base64_with_media_type():
    client, _ = _run()

    request = client.messages.last_request
    image_block = request["messages"][0]["content"][0]
    assert image_block["type"] == "image"
    assert image_block["source"]["type"] == "base64"
    assert image_block["source"]["media_type"] == "image/png"
    assert base64.standard_b64decode(image_block["source"]["data"]) == IMAGE_BYTES


def test_forces_the_record_invoice_tool_at_configured_model_and_effort():
    client, _ = _run(model="claude-sonnet-5", effort="medium")

    request = client.messages.last_request
    assert request["model"] == "claude-sonnet-5"
    assert request["output_config"] == {"effort": "medium"}
    assert request["tool_choice"] == {"type": "tool", "name": "record_invoice"}
    assert request["tools"][0]["name"] == "record_invoice"
    assert "do not compute" in request["system"].lower()


def test_defaults_are_sonnet_5_medium():
    assert DEFAULT_MODEL == "claude-sonnet-5"
    assert DEFAULT_EFFORT == "medium"


def test_parses_tool_output_into_decimals():
    _, result = _run()

    assert result.ok is True
    invoice = result.invoice
    assert invoice.vendor_name == "Nordwind Office Supplies GmbH"
    assert invoice.currency == "EUR"
    assert invoice.grand_total == Decimal("465.00")
    assert isinstance(invoice.grand_total, Decimal)
    assert [li.line_total for li in invoice.line_items] == [
        Decimal("147.00"), Decimal("267.00"), Decimal("51.00")
    ]
    assert invoice.line_items[0].quantity == Decimal("6")
    assert result.parse_error is None
    assert result.refused is False


def test_confidence_is_clamped_to_unit_interval():
    payload = record_invoice_payload("clean_office_supplies") | {"extraction_confidence": 1.4}
    _, result = _run(payload=payload)
    assert result.invoice.extraction_confidence == 1.0


def test_refusal_returns_no_invoice_without_raising():
    _, result = _run(client=refusal_client(category="cyber"))

    assert result.ok is False
    assert result.invoice is None
    assert result.refused is True
    assert result.refusal_category == "cyber"
    assert result.prompt_hash  # bookkeeping preserved for the audit trail


def test_missing_tool_call_is_a_parse_error_not_a_crash():
    _, result = _run(client=no_tool_call_client())

    assert result.ok is False
    assert result.refused is False
    assert "no record_invoice tool call" in result.parse_error


def test_malformed_tool_output_is_a_parse_error():
    _, result = _run(payload={"vendor_name": "X"})  # missing required fields

    assert result.ok is False
    assert result.parse_error is not None


def test_empty_line_items_is_a_parse_error():
    payload = record_invoice_payload("clean_office_supplies") | {"line_items": []}
    _, result = _run(payload=payload)

    assert result.ok is False
    assert "line_items" in result.parse_error


def test_prompt_hash_depends_on_image_content():
    c1 = invoice_client(record_invoice_payload("clean_office_supplies"))
    c2 = invoice_client(record_invoice_payload("clean_office_supplies"))
    r1 = extract_invoice(c1, content=b"image-one", media_type="image/png")
    r2 = extract_invoice(c2, content=b"image-one", media_type="image/png")
    r3 = extract_invoice(c2, content=b"image-two", media_type="image/png")

    assert r1.prompt_hash == r2.prompt_hash
    assert r1.prompt_hash != r3.prompt_hash
