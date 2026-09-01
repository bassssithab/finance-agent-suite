import base64
from decimal import Decimal

from expense_agent.extraction import DEFAULT_EFFORT, DEFAULT_MODEL, extract_receipt
from fakes import no_tool_call_client, receipt_client, refusal_client
from fixtures import record_receipt_payload

IMAGE_BYTES = b"\x89PNG\r\n\x1a\n-pretend-this-is-a-receipt"


def _run(payload=None, client=None, **kwargs):
    payload = payload or record_receipt_payload("compliant_taxi")
    client = client or receipt_client(payload)
    result = extract_receipt(client, content=IMAGE_BYTES, media_type="image/png", **kwargs)
    return client, result


def test_sends_image_block_as_base64_with_media_type():
    client, _ = _run()
    image_block = client.messages.last_request["messages"][0]["content"][0]
    assert image_block["type"] == "image"
    assert image_block["source"]["type"] == "base64"
    assert image_block["source"]["media_type"] == "image/png"
    assert base64.standard_b64decode(image_block["source"]["data"]) == IMAGE_BYTES


def test_forces_the_record_receipt_tool_at_configured_model_and_effort():
    client, _ = _run(model="claude-sonnet-5", effort="medium")
    request = client.messages.last_request
    assert request["model"] == "claude-sonnet-5"
    assert request["output_config"] == {"effort": "medium"}
    assert request["tool_choice"] == {"type": "tool", "name": "record_receipt"}
    assert request["tools"][0]["name"] == "record_receipt"
    assert "do not compute" in request["system"].lower()


def test_defaults_are_sonnet_5_medium():
    assert DEFAULT_MODEL == "claude-sonnet-5"
    assert DEFAULT_EFFORT == "medium"


def test_parses_tool_output_into_decimal_amount():
    _, result = _run()
    assert result.ok is True
    receipt = result.receipt
    assert receipt.vendor == "Larkspur City Cabs"
    assert receipt.currency == "USD"
    assert receipt.expense_category == "Travel - taxi"
    assert receipt.amount == Decimal("38.40")
    assert isinstance(receipt.amount, Decimal)
    assert result.parse_error is None and result.refused is False


def test_confidence_is_clamped_to_unit_interval():
    payload = record_receipt_payload("compliant_taxi") | {"extraction_confidence": 1.4}
    _, result = _run(payload=payload)
    assert result.receipt.extraction_confidence == 1.0


def test_blank_amount_becomes_zero_not_a_parse_error():
    # A blank amount is a "couldn't read it" — compliance.py flags it as a
    # missing required field; extraction must not fail the whole receipt.
    payload = record_receipt_payload("compliant_taxi") | {"amount": ""}
    _, result = _run(payload=payload)
    assert result.ok is True
    assert result.receipt.amount == Decimal("0")


def test_refusal_returns_no_receipt_without_raising():
    _, result = _run(client=refusal_client(category="cyber"))
    assert result.ok is False
    assert result.receipt is None
    assert result.refused is True
    assert result.refusal_category == "cyber"
    assert result.prompt_hash


def test_missing_tool_call_is_a_parse_error_not_a_crash():
    _, result = _run(client=no_tool_call_client())
    assert result.ok is False
    assert result.refused is False
    assert "no record_receipt tool call" in result.parse_error


def test_malformed_tool_output_is_a_parse_error():
    _, result = _run(payload={"vendor": "X"})  # missing required fields
    assert result.ok is False
    assert result.parse_error is not None


def test_prompt_hash_depends_on_image_content():
    c1 = receipt_client(record_receipt_payload("compliant_taxi"))
    c2 = receipt_client(record_receipt_payload("compliant_taxi"))
    r1 = extract_receipt(c1, content=b"image-one", media_type="image/png")
    r2 = extract_receipt(c2, content=b"image-one", media_type="image/png")
    r3 = extract_receipt(c2, content=b"image-two", media_type="image/png")
    assert r1.prompt_hash == r2.prompt_hash
    assert r1.prompt_hash != r3.prompt_hash
