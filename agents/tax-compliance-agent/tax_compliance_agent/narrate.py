"""Drafts the plain-English filing-support narrative for a period-end VAT
provision via one Claude API call, grounded in retrieved `platform/knowledge`
VAT filing-guidance chunks when any are available.

The only module that talks to the LLM. CLAUDE.md rule #4: the model writes prose
only — it is handed the already-computed output VAT, input VAT, net position and
flagged anomalies and told they are final and authoritative; it does no
arithmetic. Overconfidence is a real risk here: a VAT return has legal
consequences, so the system prompt forbids the model from saying the filing is
correct, complete, or ready to submit — that is the reviewer's and ultimately a
qualified tax professional's call — and requires it to flag an anomaly for
specialist review rather than resolve it. Citations are mandatory when context
is used (docs/ARCHITECTURE.md); an ungrounded narrative comes back with an empty
citations list and must not invent a filing rule or deadline.

`draft_filing_support_narrative` takes an already-constructed client so
callers/tests can inject a fake one — a real `anthropic.Anthropic()` is only
ever built by runner.py (lazily) or manual_live_run.py. Importing this module
never needs ANTHROPIC_API_KEY.

Model default is claude-sonnet-5 at effort "medium": the same deliberate cost
tradeoff as agents/close-agent, agents/fpa-agent and agents/vat-treatment-agent
while this agent is new — see agents/tax-compliance-agent/README.md.
"""

import hashlib
from dataclasses import dataclass
from typing import Optional

from knowledge import SearchResult

from .models import Anomaly, FilingSupportNarrative

DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_EFFORT = "medium"
MAX_TOKENS = 2048

RECORD_FILING_SUPPORT_NARRATIVE_TOOL = {
    "name": "record_filing_support_narrative",
    "description": "Record the plain-English filing-support narrative for a period-end VAT provision.",
    "input_schema": {
        "type": "object",
        "properties": {
            "position_summary": {
                "type": "string",
                "description": "3-5 sentences on the period's VAT position (output VAT, input VAT, net payable or refundable), stated as the calculation's result, never as a confirmed or final return.",
            },
            "anomaly_explanations": {
                "type": "array",
                "items": {"type": "string"},
                "description": "One entry per flagged anomaly: what it is, why it matters, and whether it needs a tax specialist's review. Empty list only when nothing was flagged.",
            },
            "specialist_review_needed": {
                "type": "boolean",
                "description": "True if any flagged anomaly warrants review by a qualified tax specialist before the return is filed.",
            },
            "citations": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Citation labels of the excerpts relied on, exactly as given. Empty when no context was provided or used.",
            },
        },
        "required": ["position_summary", "anomaly_explanations", "specialist_review_needed", "citations"],
    },
}

SYSTEM_PROMPT = """\
You are a tax-compliance assistant drafting filing-support notes for a \
period-end VAT provision. A deterministic calculation has already produced the \
output VAT (VAT on sales), the input VAT (VAT on purchases), and the net \
payable-or-refundable position, and has flagged a set of anomalies.

Rules:
1. The output VAT, input VAT, net position, the by-treatment breakdown and the \
flagged anomalies you are given are final and authoritative. Never recompute, \
adjust, restate, or dispute them, and never do arithmetic of your own.
2. Do NOT state or imply that the filing is correct, complete, accurate, \
reconciled, or ready to submit. Whether the return can be filed is the \
reviewer's decision and ultimately a qualified tax professional's - not yours. \
Do not write "this can be filed", "the return is ready", "no issues", \
"everything ties out", or anything to that effect.
3. For each flagged anomaly, explain what it is and why it matters, and say \
plainly whether it needs a tax specialist's review. An unrecognised treatment, \
a treatment/rate mismatch, or a net refundable position are NOT for you to \
resolve or explain away - flag them for specialist review and set \
specialist_review_needed to true when any such anomaly is present.
4. A net refundable position is unusual for most trading businesses. Call it \
out as worth double-checking; do not reassure the reader that it is fine.
5. When <context> contains VAT filing-guidance excerpts, ground the narrative \
in them and cite the excerpt you relied on using its citation label exactly as \
given. When no excerpts are provided, describe the position in general terms \
and do NOT assert a specific filing rule, threshold, or deadline.
6. This is a draft for human review. It is not a filed return, and it is not \
tax advice.
Always respond by calling the record_filing_support_narrative tool.
"""


def describe_position(summary: dict) -> str:
    """Deterministic natural-language description of the period's VAT position —
    used as a platform/knowledge search query (CLAUDE.md rule #4: plain code
    builds the query, the LLM only writes prose from what it is handed)."""
    return (
        f"Period-end VAT provision for {summary.get('period_label')}: output VAT "
        f"{summary.get('output_vat_total')}, input VAT {summary.get('input_vat_total')}, "
        f"net {summary.get('net_vat')} ({summary.get('position')}). "
        "What VAT filing guidance applies to preparing a period return, netting output "
        "VAT against recoverable input VAT, handling a refundable position, and "
        "escalating an unresolved transaction to a tax specialist?"
    )


def describe_anomaly(anomaly: Anomaly) -> str:
    """Deterministic query for one flagged anomaly."""
    return (
        f"VAT provision anomaly ({anomaly.code}): {anomaly.detail}. "
        "What VAT filing guidance applies to this kind of exception, and when must it "
        "be referred to a tax specialist rather than resolved in preparation?"
    )


def _format_context(chunks: list[SearchResult]) -> str:
    if not chunks:
        return "(no VAT filing-guidance excerpts were retrieved for this provision)"
    return "\n\n".join(
        f'<excerpt citation="{r.chunk.citation}">\n{r.chunk.text}\n</excerpt>'
        for r in chunks
    )


def _format_breakdown(by_treatment: dict) -> str:
    rows = []
    for treatment, sides in by_treatment.items():
        for side, cell in sides.items():
            if cell["count"]:
                rows.append(
                    f"  {treatment} / {side}: {cell['count']} txn, net {cell['amount']}, VAT {cell['vat']}"
                )
    return "\n".join(rows) or "  (no recognised transactions)"


def build_user_prompt(summary: dict, by_treatment: dict, anomalies: list, chunks: list) -> str:
    if anomalies:
        anomaly_rows = "\n".join(
            f"- {a['code']}"
            + (f" (transaction {a['transaction_id']})" if a["transaction_id"] else " (whole period)")
            + f": {a['detail']}"
            for a in anomalies
        )
    else:
        anomaly_rows = "(no anomalies were flagged)"

    excluded = summary.get("transactions_excluded_from_totals") or []
    excluded_note = (
        f"\nTransactions excluded from the totals (could not be classified): "
        f"{', '.join(excluded)}.\n"
        if excluded else "\n"
    )

    return (
        f"<context>\n{_format_context(chunks)}\n</context>\n\n"
        f"Period-end VAT provision for {summary.get('period_label')} "
        f"({summary.get('date_range', {}).get('from')} to {summary.get('date_range', {}).get('to')}). "
        "All figures below are final and authoritative — do not recompute or dispute them.\n\n"
        f"Output VAT (on sales): {summary.get('output_vat_total')}\n"
        f"Input VAT (on purchases): {summary.get('input_vat_total')}\n"
        f"Net VAT: {summary.get('net_vat')} — position: {summary.get('position')}\n"
        f"{excluded_note}"
        f"By treatment:\n{_format_breakdown(by_treatment)}\n\n"
        f"Flagged anomalies:\n{anomaly_rows}\n\n"
        "Call record_filing_support_narrative. Summarise the position without "
        "asserting the return is correct or ready to file, explain each anomaly and "
        "whether it needs a specialist, and set specialist_review_needed accordingly."
    )


@dataclass(frozen=True)
class NarrativeResult:
    narrative: Optional[FilingSupportNarrative]  # None on refusal / parse failure
    model: str
    prompt_hash: str
    chunk_ids: list
    citations: list
    refused: bool = False
    refusal_category: Optional[str] = None
    parse_error: Optional[str] = None


def _parse_narrative(payload: dict) -> FilingSupportNarrative:
    position_summary = payload["position_summary"]
    if not isinstance(position_summary, str) or not position_summary.strip():
        raise ValueError("record_filing_support_narrative returned an empty position_summary")

    raw_flag = payload["specialist_review_needed"]
    if not isinstance(raw_flag, bool):
        raise ValueError("specialist_review_needed must be a boolean")

    def _str_list(value, name: str) -> list:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError(f"{name} must be a list")
        return [str(v) for v in value]

    return FilingSupportNarrative(
        position_summary=str(position_summary),
        anomaly_explanations=_str_list(payload.get("anomaly_explanations"), "anomaly_explanations"),
        specialist_review_needed=raw_flag,
        citations=_str_list(payload.get("citations"), "citations"),
    )


def draft_filing_support_narrative(
    client,
    summary: dict,
    by_treatment: dict,
    anomalies: list,
    chunks: list,
    model: str = DEFAULT_MODEL,
    effort: str = DEFAULT_EFFORT,
) -> NarrativeResult:
    chunk_ids = [r.chunk.chunk_id for r in chunks]
    citations = [r.chunk.citation for r in chunks]

    user_prompt = build_user_prompt(summary, by_treatment, anomalies, chunks)
    prompt_hash = hashlib.sha256((SYSTEM_PROMPT + user_prompt).encode("utf-8")).hexdigest()

    response = client.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        output_config={"effort": effort},
        tools=[RECORD_FILING_SUPPORT_NARRATIVE_TOOL],
        tool_choice={"type": "tool", "name": "record_filing_support_narrative"},
        messages=[{"role": "user", "content": user_prompt}],
    )

    base = dict(model=model, prompt_hash=prompt_hash, chunk_ids=chunk_ids, citations=citations)

    if response.stop_reason == "refusal":
        stop_details = getattr(response, "stop_details", None)
        category = getattr(stop_details, "category", None) if stop_details else None
        return NarrativeResult(narrative=None, refused=True, refusal_category=category, **base)

    tool_use = next(
        (b for b in response.content if getattr(b, "type", None) == "tool_use"),
        None,
    )
    if tool_use is None:
        return NarrativeResult(
            narrative=None,
            parse_error="no record_filing_support_narrative tool call in the response",
            **base,
        )

    try:
        narrative = _parse_narrative(tool_use.input)
    except (KeyError, TypeError, ValueError) as exc:
        return NarrativeResult(
            narrative=None,
            parse_error=f"could not parse record_filing_support_narrative output: {exc}",
            **base,
        )

    return NarrativeResult(narrative=narrative, **base)
