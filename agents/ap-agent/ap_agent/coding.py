"""Optional GL coding: for each invoice line, suggest the account it should be
booked to, grounded strictly in retrieved `platform/knowledge` chart-of-accounts
chunks.

The second (and last) module that talks to the LLM. CLAUDE.md rule #4: the
model classifies/explains only — it assigns an account and cites the chunk it
relied on; it does no arithmetic. Citations are mandatory
(docs/ARCHITECTURE.md): a suggestion with no supporting chunk must come back
with a null account and say so, never a guess from general accounting
knowledge.

If retrieval finds nothing relevant, the runner skips this step entirely — it
is genuinely optional (see the task spec / README).
"""

import hashlib
from dataclasses import dataclass
from typing import Optional

from knowledge import SearchResult

from .models import ExtractedInvoice, GLCodingSuggestion

DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_EFFORT = "medium"
MAX_TOKENS = 2048

RECORD_GL_CODING_TOOL = {
    "name": "record_gl_coding",
    "description": "Record a suggested GL account for each invoice line item.",
    "input_schema": {
        "type": "object",
        "properties": {
            "suggestions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "line_index": {"type": "integer", "description": "0-based index of the line item this applies to."},
                        "account_code": {"type": ["string", "null"], "description": "Account code from the provided chart of accounts, or null if none of the excerpts cover this line."},
                        "account_name": {"type": ["string", "null"]},
                        "rationale": {"type": "string", "description": "Why this account, or — if null — why the excerpts don't cover the line."},
                        "citation": {"type": ["string", "null"], "description": "Citation label of the excerpt relied on, exactly as given. Null when account_code is null."},
                    },
                    "required": ["line_index", "account_code", "account_name", "rationale", "citation"],
                },
            },
        },
        "required": ["suggestions"],
    },
}

SYSTEM_PROMPT = """\
You are an accounts-payable GL-coding assistant. For each invoice line item, \
suggest the general-ledger account it should be coded to, using ONLY the \
chart-of-accounts excerpts provided in <context>.

Rules:
1. Use only the provided excerpts. Never code a line from general accounting \
knowledge.
2. Cite the excerpt you relied on for each suggestion, using its citation \
label exactly as given.
3. If the excerpts don't cover a line item, return account_code and citation \
as null and explain the gap in the rationale — do not guess.
4. This is a draft for a human AP approver, not a final coding decision.
Always respond by calling the record_gl_coding tool.
"""


@dataclass(frozen=True)
class CodingResult:
    suggestions: Optional[list[GLCodingSuggestion]]  # None on refusal / parse failure
    model: str
    prompt_hash: str
    chunk_ids: list[str]
    citations: list[str]
    refused: bool = False
    refusal_category: Optional[str] = None
    parse_error: Optional[str] = None


def _format_context(chunks: list[SearchResult]) -> str:
    return "\n\n".join(
        f'<excerpt citation="{r.chunk.citation}">\n{r.chunk.text}\n</excerpt>'
        for r in chunks
    )


def build_user_prompt(invoice: ExtractedInvoice, chunks: list[SearchResult]) -> str:
    lines = "\n".join(
        f"{i}. {li.description}" for i, li in enumerate(invoice.line_items)
    )
    return (
        f"<context>\n{_format_context(chunks)}\n</context>\n\n"
        f"Vendor: {invoice.vendor_name}\n"
        f"Invoice line items:\n{lines}\n\n"
        "Suggest a GL account for each line item by calling record_gl_coding."
    )


def _parse_suggestions(payload: dict, invoice: ExtractedInvoice) -> list[GLCodingSuggestion]:
    raw = payload["suggestions"]
    if not isinstance(raw, list):
        raise ValueError("record_gl_coding returned no suggestions list")

    line_count = len(invoice.line_items)
    by_index: dict[int, GLCodingSuggestion] = {}
    for entry in raw:
        idx = int(entry["line_index"])
        if not 0 <= idx < line_count:
            raise ValueError(f"suggestion line_index {idx} out of range")
        code = entry.get("account_code")
        citation = entry.get("citation")
        by_index[idx] = GLCodingSuggestion(
            line_index=idx,
            description=invoice.line_items[idx].description,
            account_code=str(code) if code not in (None, "") else None,
            account_name=(
                str(entry["account_name"])
                if entry.get("account_name") not in (None, "")
                else None
            ),
            rationale=str(entry.get("rationale", "")),
            citation=str(citation) if citation not in (None, "") else None,
        )

    # Return one suggestion per line, in line order; fill any the model omitted.
    return [
        by_index.get(
            i,
            GLCodingSuggestion(
                line_index=i,
                description=invoice.line_items[i].description,
                account_code=None,
                account_name=None,
                rationale="No suggestion returned for this line.",
                citation=None,
            ),
        )
        for i in range(line_count)
    ]


def suggest_coding(
    client,
    invoice: ExtractedInvoice,
    chunks: list[SearchResult],
    model: str = DEFAULT_MODEL,
    effort: str = DEFAULT_EFFORT,
) -> CodingResult:
    chunk_ids = [r.chunk.chunk_id for r in chunks]
    citations = [r.chunk.citation for r in chunks]

    user_prompt = build_user_prompt(invoice, chunks)
    prompt_hash = hashlib.sha256(
        (SYSTEM_PROMPT + user_prompt).encode("utf-8")
    ).hexdigest()

    response = client.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        output_config={"effort": effort},
        tools=[RECORD_GL_CODING_TOOL],
        tool_choice={"type": "tool", "name": "record_gl_coding"},
        messages=[{"role": "user", "content": user_prompt}],
    )

    base = dict(model=model, prompt_hash=prompt_hash, chunk_ids=chunk_ids, citations=citations)

    if response.stop_reason == "refusal":
        stop_details = getattr(response, "stop_details", None)
        category = getattr(stop_details, "category", None) if stop_details else None
        return CodingResult(suggestions=None, refused=True, refusal_category=category, **base)

    tool_use = next(
        (b for b in response.content if getattr(b, "type", None) == "tool_use"),
        None,
    )
    if tool_use is None:
        return CodingResult(
            suggestions=None, parse_error="no record_gl_coding tool call in the response", **base
        )

    try:
        suggestions = _parse_suggestions(tool_use.input, invoice)
    except (KeyError, TypeError, ValueError) as exc:
        return CodingResult(
            suggestions=None, parse_error=f"could not parse record_gl_coding output: {exc}", **base
        )

    return CodingResult(suggestions=suggestions, **base)
