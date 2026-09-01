"""Drafts a dunning email for each flagged invoice via one Claude API call,
grounded in retrieved `platform/knowledge` collections-policy chunks when any
are available.

The only module that talks to the LLM. CLAUDE.md rule #4: the model writes prose
only — it is handed the already-computed amount, due date, days-overdue and the
tone tier `aging.py` assigned, and told they are authoritative; it does no
arithmetic and never re-picks the tone. Citations are mandatory when context is
used (docs/ARCHITECTURE.md): an email grounded in an excerpt must name that
excerpt; an ungrounded email (no excerpts retrieved) comes back with an empty
citations list and must not invent a policy, a late fee, an interest charge, or
a legal/credit-bureau threat.

`draft_dunning_emails` takes an already-constructed client so callers/tests can
inject a fake one — a real `anthropic.Anthropic()` is only ever built by
runner.py (lazily, and only when there is at least one flagged invoice) or
manual_live_run.py. Importing this module never needs ANTHROPIC_API_KEY.

Model default is claude-sonnet-5 at effort "medium": the same deliberate cost
tradeoff as agents/close-agent, agents/controls-sox-agent and agents/ap-agent
while this agent is new — see agents/ar-collections-agent/README.md.
"""

import hashlib
from dataclasses import dataclass
from typing import Optional

from knowledge import SearchResult

from .models import DunningDraft, InvoiceAging

DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_EFFORT = "medium"
MAX_TOKENS = 4096

_TONE_GUIDANCE = {
    "reminder": (
        "a gentle, friendly reminder — assume good faith and a possible oversight; "
        "no pressure, no consequences mentioned"
    ),
    "firm": (
        "firm and direct — the account is materially overdue; state clearly that "
        "payment is now required and ask for a payment date, while staying professional"
    ),
    "formal": (
        "formal and serious — this is a final-notice tone; make the urgency "
        "unmistakable and request immediate payment, but do NOT threaten a specific "
        "action (legal action, collections agency, credit reporting, service "
        "suspension, interest, or fees) unless a policy excerpt in <context> "
        "explicitly authorises it"
    ),
}

RECORD_DUNNING_DRAFTS_TOOL = {
    "name": "record_dunning_drafts",
    "description": "Record a drafted dunning (collections) email for each flagged overdue invoice.",
    "input_schema": {
        "type": "object",
        "properties": {
            "drafts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "invoice_id": {"type": "string", "description": "Invoice id of the flagged invoice, exactly as given."},
                        "tone": {"type": "string", "description": "The tone tier you were asked to write in for this invoice, exactly as given (reminder, firm, or formal)."},
                        "subject": {"type": "string", "description": "The email subject line."},
                        "body": {"type": "string", "description": "The full email body, ready for a collections clerk to review and send. Plain text, greeting to sign-off."},
                        "citations": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Citation labels of the excerpts relied on, exactly as given. Empty when no context was provided or used.",
                        },
                    },
                    "required": ["invoice_id", "tone", "subject", "body", "citations"],
                },
            },
        },
        "required": ["drafts"],
    },
}

SYSTEM_PROMPT = """\
You are an accounts-receivable collections assistant. A collections clerk has \
flagged a set of overdue customer invoices for a dunning email. For each \
flagged invoice, draft one email.

Rules:
1. Write a complete email (subject + body) the clerk can review and send, \
addressed to the customer's accounts-payable contact. Keep it concise.
2. The amount, currency, invoice date, due date and days-overdue figures you \
are given are final and authoritative. Never recompute, correct, or dispute \
them, and never do arithmetic of your own (including interest or running \
balances).
3. Write in the tone tier named for that invoice, and only that tier. The tier \
was assigned by a deterministic rule from how overdue the invoice is — do not \
soften or escalate it.
4. When <context> contains collections-policy excerpts, ground the email in \
them (payment terms, escalation steps, approved late-fee or interest language) \
and cite the excerpt you relied on using its citation label exactly as given. \
When no excerpts are provided, write a plain factual payment request and do \
NOT invent or imply a late fee, an interest charge, a legal step, a collections \
-agency referral, credit-bureau reporting, or a service suspension.
5. Do not allege fraud or bad faith, and do not concede or negotiate a \
discount, waiver, or payment plan — routing those is the clerk's job.
6. This is a draft for human review. It is not sent by you.
Always respond by calling the record_dunning_drafts tool.
"""


def _fmt_last_payment(ia: InvoiceAging) -> str:
    if ia.last_payment_date is None:
        return "no payment recorded against the account"
    return f"last payment {ia.last_payment_date} ({ia.days_since_last_payment} days ago)"


def describe_invoice(ia: InvoiceAging) -> str:
    """Deterministic natural-language description of one flagged invoice — used
    as the platform/knowledge search query (CLAUDE.md rule #4: plain code builds
    the query, the LLM only writes prose from what it is handed)."""
    return (
        f"Overdue accounts-receivable invoice {ia.invoice_id} for customer {ia.customer}. "
        f"Amount {ia.amount} {ia.currency}, due {ia.due_date}, {ia.days_overdue} days past due "
        f"(aging bucket {ia.bucket}). "
        "What collections and dunning policy applies: payment terms, reminder and "
        "escalation steps, late-payment fees or interest, and approved wording for "
        "contacting a customer about an overdue balance?"
    )


def _format_context(chunks: list[SearchResult]) -> str:
    if not chunks:
        return "(no collections-policy excerpts were retrieved for these invoices)"
    return "\n\n".join(
        f'<excerpt citation="{r.chunk.citation}">\n{r.chunk.text}\n</excerpt>'
        for r in chunks
    )


def build_user_prompt(flagged: list[InvoiceAging], chunks: list[SearchResult]) -> str:
    rows = []
    for ia in flagged:
        rows.append(
            f"- invoice {ia.invoice_id} | customer {ia.customer} | "
            f"amount {ia.amount} {ia.currency} | invoice date {ia.invoice_date} | "
            f"due {ia.due_date} | {ia.days_overdue} days overdue | bucket {ia.bucket} | "
            f"{_fmt_last_payment(ia)}\n"
            f"  write in tone tier: {ia.tone_tier} — {_TONE_GUIDANCE[ia.tone_tier]}\n"
            f"  flagged because: {'; '.join(ia.flag_reasons)}"
        )
    figures = "\n".join(rows)
    return (
        f"<context>\n{_format_context(chunks)}\n</context>\n\n"
        "Flagged overdue invoices. These figures are final and authoritative — do not "
        f"recompute or dispute them:\n{figures}\n\n"
        "Call record_dunning_drafts with one entry per flagged invoice above, matching "
        "each on its invoice_id exactly and using the tone tier named for it."
    )


@dataclass(frozen=True)
class DunningResult:
    drafts: Optional[list[DunningDraft]]  # None on refusal / parse failure
    model: str
    prompt_hash: str
    chunk_ids: list[str]
    citations: list[str]
    refused: bool = False
    refusal_category: Optional[str] = None
    parse_error: Optional[str] = None


def _parse_drafts(payload: dict, flagged: list[InvoiceAging]) -> list[DunningDraft]:
    raw = payload["drafts"]
    if not isinstance(raw, list):
        raise ValueError("record_dunning_drafts returned no drafts list")

    tone_by_id = {ia.invoice_id: ia.tone_tier for ia in flagged}
    by_id: dict[str, DunningDraft] = {}
    for entry in raw:
        invoice_id = str(entry["invoice_id"])
        if invoice_id not in tone_by_id:
            raise ValueError(f"draft for unknown invoice {invoice_id!r}")
        citations = entry.get("citations") or []
        by_id[invoice_id] = DunningDraft(
            invoice_id=invoice_id,
            # The tone tier is deterministic — trust ours, not the model's echo.
            tone=tone_by_id[invoice_id],
            subject=str(entry.get("subject", "")),
            body=str(entry.get("body", "")),
            citations=[str(c) for c in citations],
        )

    # One draft per flagged invoice, in flagged order; fill any the model omitted.
    return [
        by_id.get(
            ia.invoice_id,
            DunningDraft(
                invoice_id=ia.invoice_id,
                tone=ia.tone_tier,
                subject="No draft returned for this invoice.",
                body="No draft returned for this invoice.",
                citations=[],
            ),
        )
        for ia in flagged
    ]


def draft_dunning_emails(
    client,
    flagged: list[InvoiceAging],
    chunks: list[SearchResult],
    model: str = DEFAULT_MODEL,
    effort: str = DEFAULT_EFFORT,
) -> DunningResult:
    chunk_ids = [r.chunk.chunk_id for r in chunks]
    citations = [r.chunk.citation for r in chunks]

    user_prompt = build_user_prompt(flagged, chunks)
    prompt_hash = hashlib.sha256((SYSTEM_PROMPT + user_prompt).encode("utf-8")).hexdigest()

    response = client.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        output_config={"effort": effort},
        tools=[RECORD_DUNNING_DRAFTS_TOOL],
        tool_choice={"type": "tool", "name": "record_dunning_drafts"},
        messages=[{"role": "user", "content": user_prompt}],
    )

    base = dict(model=model, prompt_hash=prompt_hash, chunk_ids=chunk_ids, citations=citations)

    if response.stop_reason == "refusal":
        stop_details = getattr(response, "stop_details", None)
        category = getattr(stop_details, "category", None) if stop_details else None
        return DunningResult(drafts=None, refused=True, refusal_category=category, **base)

    tool_use = next(
        (b for b in response.content if getattr(b, "type", None) == "tool_use"),
        None,
    )
    if tool_use is None:
        return DunningResult(
            drafts=None,
            parse_error="no record_dunning_drafts tool call in the response",
            **base,
        )

    try:
        drafts = _parse_drafts(tool_use.input, flagged)
    except (KeyError, TypeError, ValueError) as exc:
        return DunningResult(
            drafts=None,
            parse_error=f"could not parse record_dunning_drafts output: {exc}",
            **base,
        )

    return DunningResult(drafts=drafts, **base)
