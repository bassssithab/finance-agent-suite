"""Vision extraction: an invoice image in, a structured `ExtractedInvoice` out.

This is one of two modules that talk to the LLM (the other is `coding.py`).
CLAUDE.md rule #4: the model only transcribes what is visibly printed on the
invoice — it is explicitly told not to compute, correct, or reconcile any
figure. The arithmetic check that follows is plain code (`sanity.py`).

The model returns its answer through a forced `record_invoice` tool call, not
as prose, so the result is structured JSON rather than something to parse out
of free text. `extract_invoice` takes an already-constructed client so
callers/tests can inject a fake one (see `evals/fakes.py`); a real
`anthropic.Anthropic()` is only ever built by `runner.py` (lazily) or
`manual_live_run.py`, so importing this module never needs ANTHROPIC_API_KEY
or even the anthropic package.

Model default is claude-sonnet-5 at effort "medium" — the same deliberate cost
tradeoff as `agents/vat-treatment-agent` and `agents/technical-accounting-agent`
while this agent is new and still being tuned (see the README), not a claim
that Sonnet is the right long-term tier for invoice OCR.
"""

import base64
import hashlib
from dataclasses import dataclass
from typing import Optional

from .models import ExtractedInvoice, InvoiceLineItem
from .money import parse_decimal

DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_EFFORT = "medium"
MAX_TOKENS = 2048

RECORD_INVOICE_TOOL = {
    "name": "record_invoice",
    "description": (
        "Record the data transcribed from the invoice image. Report exactly "
        "what is printed; do not compute or correct any figure."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "vendor_name": {"type": "string", "description": "Supplier / vendor name as printed. Empty string if not legible."},
            "invoice_number": {"type": "string", "description": "Invoice number / reference as printed. Empty string if not present."},
            "invoice_date": {"type": "string", "description": "Invoice date exactly as printed on the document."},
            "currency": {"type": "string", "description": "ISO 4217 code (USD, EUR, ...) if determinable, else the symbol or word shown."},
            "line_items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "description": {"type": "string"},
                        "quantity": {"type": "string", "description": "As printed. '1' if the invoice shows no quantity for the line."},
                        "unit_price": {"type": "string", "description": "As printed, digits only where possible."},
                        "line_total": {"type": "string", "description": "The line's extended amount as printed."},
                    },
                    "required": ["description", "quantity", "unit_price", "line_total"],
                },
            },
            "grand_total": {"type": "string", "description": "The invoice total as printed."},
            "extraction_confidence": {
                "type": "number",
                "description": "0.0–1.0: your confidence that the fields above match the image. Lower it for faint scans, handwriting, or ambiguous layouts.",
            },
        },
        "required": [
            "vendor_name", "invoice_number", "invoice_date", "currency",
            "line_items", "grand_total", "extraction_confidence",
        ],
    },
}

SYSTEM_PROMPT = """\
You extract structured data from a single invoice image for an accounts-payable \
workflow. Transcribe only what is visibly printed on the invoice.

Rules:
1. Do not compute, correct, or reconcile any figure. If the invoice's own \
arithmetic looks wrong (a line total or the grand total doesn't add up), \
transcribe it exactly as shown — a downstream check handles that, not you.
2. If a field is missing or illegible, say so rather than guessing: use an \
empty string for text you cannot read, and lower extraction_confidence to \
reflect the uncertainty.
3. Always respond by calling the record_invoice tool. Do not answer in prose.
"""

USER_TEXT = (
    "Extract every field from this invoice image by calling record_invoice. "
    "Transcribe the figures exactly as printed — do not fix arithmetic."
)


@dataclass(frozen=True)
class ExtractionResult:
    invoice: Optional[ExtractedInvoice]
    model: str
    prompt_hash: str
    refused: bool = False
    refusal_category: Optional[str] = None
    parse_error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.invoice is not None


def build_image_block(content: bytes, media_type: str) -> dict:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": base64.standard_b64encode(content).decode("ascii"),
        },
    }


def _prompt_hash(media_type: str, content: bytes) -> str:
    # Identifies the (prompt, image) pair for the audit trail without putting
    # the image bytes in the log — the content sha256 stands in for them.
    material = "\n".join([
        SYSTEM_PROMPT,
        USER_TEXT,
        media_type,
        hashlib.sha256(content).hexdigest(),
    ])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _parse_invoice(payload: dict) -> ExtractedInvoice:
    raw_lines = payload["line_items"]
    if not isinstance(raw_lines, list) or not raw_lines:
        raise ValueError("record_invoice returned no line_items")

    line_items = [
        InvoiceLineItem(
            description=str(li["description"]),
            quantity=parse_decimal(li["quantity"]),
            unit_price=parse_decimal(li["unit_price"]),
            line_total=parse_decimal(li["line_total"]),
        )
        for li in raw_lines
    ]

    confidence = float(payload["extraction_confidence"])
    confidence = max(0.0, min(1.0, confidence))

    return ExtractedInvoice(
        vendor_name=str(payload["vendor_name"]),
        invoice_number=str(payload["invoice_number"]),
        invoice_date=str(payload["invoice_date"]),
        currency=str(payload["currency"]),
        line_items=line_items,
        grand_total=parse_decimal(payload["grand_total"]),
        extraction_confidence=confidence,
    )


def extract_invoice(
    client,
    *,
    content: bytes,
    media_type: str,
    model: str = DEFAULT_MODEL,
    effort: str = DEFAULT_EFFORT,
) -> ExtractionResult:
    prompt_hash = _prompt_hash(media_type, content)
    user_content = [
        build_image_block(content, media_type),
        {"type": "text", "text": USER_TEXT},
    ]

    response = client.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        output_config={"effort": effort},
        tools=[RECORD_INVOICE_TOOL],
        tool_choice={"type": "tool", "name": "record_invoice"},
        messages=[{"role": "user", "content": user_content}],
    )

    if response.stop_reason == "refusal":
        stop_details = getattr(response, "stop_details", None)
        category = getattr(stop_details, "category", None) if stop_details else None
        return ExtractionResult(
            invoice=None, model=model, prompt_hash=prompt_hash,
            refused=True, refusal_category=category,
        )

    tool_use = next(
        (b for b in response.content if getattr(b, "type", None) == "tool_use"),
        None,
    )
    if tool_use is None:
        return ExtractionResult(
            invoice=None, model=model, prompt_hash=prompt_hash,
            parse_error="no record_invoice tool call in the response",
        )

    try:
        invoice = _parse_invoice(tool_use.input)
    except (KeyError, TypeError, ValueError) as exc:
        return ExtractionResult(
            invoice=None, model=model, prompt_hash=prompt_hash,
            parse_error=f"could not parse record_invoice output: {exc}",
        )

    return ExtractionResult(invoice=invoice, model=model, prompt_hash=prompt_hash)
