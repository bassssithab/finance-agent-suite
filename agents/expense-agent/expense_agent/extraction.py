"""Vision extraction: a receipt image in, a structured `ExtractedReceipt` out.

One of two modules that talk to the LLM (the other is `explain.py`). CLAUDE.md
rule #4: the model only transcribes what is visibly printed on the receipt and
infers a category label — it is explicitly told not to compute, correct, or
judge any figure. The policy checks that follow are plain code
(`compliance.py`).

The model returns its answer through a forced `record_receipt` tool call, not
as prose, so the result is structured JSON rather than something to parse out of
free text. `extract_receipt` takes an already-constructed client so
callers/tests can inject a fake one (see `evals/fakes.py`); a real
`anthropic.Anthropic()` is only ever built by `runner.py` (lazily) or
`manual_live_run.py`, so importing this module never needs ANTHROPIC_API_KEY or
even the anthropic package.

Model default is claude-sonnet-5 at effort "medium" — the same deliberate cost
tradeoff as `agents/ap-agent` and `agents/vat-treatment-agent` while this agent
is new (see the README), not a claim that Sonnet is the right long-term tier for
receipt OCR.
"""

import base64
import hashlib
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from .models import ExtractedReceipt
from .money import parse_decimal

DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_EFFORT = "medium"
MAX_TOKENS = 1024

RECORD_RECEIPT_TOOL = {
    "name": "record_receipt",
    "description": (
        "Record the data transcribed from the receipt image. Report exactly "
        "what is printed; do not compute, correct, or judge any figure."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "vendor": {"type": "string", "description": "Merchant / vendor name as printed. Empty string if not legible."},
            "date": {"type": "string", "description": "Transaction date exactly as printed on the receipt. Empty string if not present."},
            "amount": {"type": "string", "description": "The receipt total as printed, digits only where possible. Empty string if not legible."},
            "currency": {"type": "string", "description": "ISO 4217 code (USD, EUR, ...) if determinable, else the symbol or word shown. Empty string if not present."},
            "expense_category": {
                "type": "string",
                "description": "Your best guess at the expense category from the vendor and what was purchased, e.g. 'Meals', 'Travel - taxi', 'Lodging', 'Office supplies'. Empty string only if there is nothing to go on.",
            },
            "extraction_confidence": {
                "type": "number",
                "description": "0.0-1.0: your confidence that the fields above match the image. Lower it for faint scans, handwriting, crumpled receipts, or ambiguous layouts.",
            },
        },
        "required": [
            "vendor", "date", "amount", "currency",
            "expense_category", "extraction_confidence",
        ],
    },
}

SYSTEM_PROMPT = """\
You extract structured data from a single expense-receipt image for a travel- \
and-expense workflow. Transcribe only what is visibly printed on the receipt.

Rules:
1. Do not compute, correct, or reconcile any figure, and do not decide whether \
the expense is within policy - a downstream check handles that, not you. \
Transcribe the total exactly as shown.
2. expense_category is the one field you infer rather than transcribe: give \
your best guess from the vendor name and any line descriptions. Keep it to a \
short label.
3. If a field is missing or illegible, say so rather than guessing: use an \
empty string for text you cannot read, and lower extraction_confidence to \
reflect the uncertainty.
4. Always respond by calling the record_receipt tool. Do not answer in prose.
"""

USER_TEXT = (
    "Extract every field from this receipt image by calling record_receipt. "
    "Transcribe the figures exactly as printed - do not fix or judge anything."
)


@dataclass(frozen=True)
class ExtractionResult:
    receipt: Optional[ExtractedReceipt]
    model: str
    prompt_hash: str
    refused: bool = False
    refusal_category: Optional[str] = None
    parse_error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.receipt is not None


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


def _parse_receipt(payload: dict) -> ExtractedReceipt:
    confidence = float(payload["extraction_confidence"])
    confidence = max(0.0, min(1.0, confidence))

    raw_amount = payload["amount"]
    # A blank amount is a legitimate "couldn't read it" — keep it as Decimal("0")
    # so the required-fields check (compliance.py) is the one that flags it,
    # rather than raising here and losing the rest of the extraction.
    amount = parse_decimal(raw_amount) if str(raw_amount).strip() else Decimal("0")

    return ExtractedReceipt(
        vendor=str(payload["vendor"]),
        date=str(payload["date"]),
        amount=amount,
        currency=str(payload["currency"]),
        expense_category=str(payload["expense_category"]),
        extraction_confidence=confidence,
    )


def extract_receipt(
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
        tools=[RECORD_RECEIPT_TOOL],
        tool_choice={"type": "tool", "name": "record_receipt"},
        messages=[{"role": "user", "content": user_content}],
    )

    if response.stop_reason == "refusal":
        stop_details = getattr(response, "stop_details", None)
        category = getattr(stop_details, "category", None) if stop_details else None
        return ExtractionResult(
            receipt=None, model=model, prompt_hash=prompt_hash,
            refused=True, refusal_category=category,
        )

    tool_use = next(
        (b for b in response.content if getattr(b, "type", None) == "tool_use"),
        None,
    )
    if tool_use is None:
        return ExtractionResult(
            receipt=None, model=model, prompt_hash=prompt_hash,
            parse_error="no record_receipt tool call in the response",
        )

    try:
        receipt = _parse_receipt(tool_use.input)
    except (KeyError, TypeError, ValueError) as exc:
        return ExtractionResult(
            receipt=None, model=model, prompt_hash=prompt_hash,
            parse_error=f"could not parse record_receipt output: {exc}",
        )

    return ExtractionResult(receipt=receipt, model=model, prompt_hash=prompt_hash)
