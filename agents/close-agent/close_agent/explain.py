"""Drafts a plain-English explanation for each flagged variance via one Claude
API call, grounded in retrieved `platform/knowledge` accounting-policy chunks
when any are available.

The only module that talks to the LLM. CLAUDE.md rule #4: the model explains
only — it is handed the already-computed budget/actual/variance figures and
told they are authoritative; it does no arithmetic. Citations are mandatory
when context is used (docs/ARCHITECTURE.md): an explanation grounded in an
excerpt must name that excerpt; an ungrounded explanation (no excerpts
retrieved) comes back with an empty citations list and must not invent policy.

`draft_explanations` takes an already-constructed client so callers/tests can
inject a fake one — a real `anthropic.Anthropic()` is only ever built by
runner.py (lazily, and only when there is something to explain) or
manual_live_run.py. Importing this module never needs ANTHROPIC_API_KEY.

Model default is claude-sonnet-5 at effort "medium": the same deliberate cost
tradeoff as agents/ap-agent, agents/vat-treatment-agent and
agents/technical-accounting-agent while this agent is new — see
agents/close-agent/README.md.
"""

import hashlib
from dataclasses import dataclass
from typing import Optional

from knowledge import SearchResult

from .models import LineVariance, VarianceExplanation

DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_EFFORT = "medium"
MAX_TOKENS = 2048

RECORD_EXPLANATIONS_TOOL = {
    "name": "record_variance_explanations",
    "description": "Record a plain-English explanation for each flagged budget-vs-actual variance.",
    "input_schema": {
        "type": "object",
        "properties": {
            "explanations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "account": {"type": "string", "description": "Account code of the flagged line, exactly as given."},
                        "line_item": {"type": "string", "description": "Line-item label of the flagged line, exactly as given."},
                        "explanation": {"type": "string", "description": "2-4 plain sentences explaining the variance for a controller's close file."},
                        "citations": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Citation labels of the excerpts relied on, exactly as given. Empty when no context was provided or used.",
                        },
                        "primary_drivers": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Short phrases naming the main drivers of the variance, where identifiable.",
                        },
                    },
                    "required": ["account", "line_item", "explanation", "citations", "primary_drivers"],
                },
            },
        },
        "required": ["explanations"],
    },
}

SYSTEM_PROMPT = """\
You are a financial-close assistant. A controller has flagged a set of \
budget-vs-actual variances for a period. For each flagged line, draft a short \
plain-English explanation for the close file.

Rules:
1. Write 2-4 sentences per line, in plain language a reviewer can drop \
straight into the close working papers.
2. The budget, actual, variance and percentage figures you are given are \
final and authoritative. Never recompute, correct, or dispute them, and never \
do arithmetic of your own.
3. When <context> contains accounting-policy excerpts, ground your \
explanation in them and cite the excerpt you relied on using its citation \
label exactly as given. When no excerpts are provided, explain the variance \
in general operational terms and do NOT assert a specific company policy or \
an accounting-standard treatment — say plainly when a definitive explanation \
would need information you do not have.
4. Do not conclude that a variance is immaterial, acceptable, within \
tolerance, or expected — materiality and acceptability are the reviewer's \
judgement, not yours.
5. This is a draft for human review, not a final close position.
Always respond by calling the record_variance_explanations tool.
"""


def _pct_text(lv: LineVariance) -> str:
    if lv.pct_variance is None:
        return "n/a (zero budget)"
    return f"{lv.pct_variance * 100:.1f}%"


def describe_variance(lv: LineVariance) -> str:
    """Deterministic natural-language description of one flagged line — used as
    the platform/knowledge search query (CLAUDE.md rule #4: plain code builds
    the query, the LLM only writes prose from what it is handed)."""
    category = f"; category {lv.category}" if lv.category else ""
    return (
        f"Budget variance for account {lv.account} {lv.line_item}{category}. "
        f"Budget {lv.budget_amount} {lv.currency}, actual {lv.actual_amount} {lv.currency}, "
        f"variance {lv.variance} ({_pct_text(lv)}), {lv.direction.replace('_', ' ')}. "
        f"Period {lv.period}. "
        "What accounting-policy guidance applies to reviewing and documenting this variance?"
    )


def _format_context(chunks: list[SearchResult]) -> str:
    if not chunks:
        return "(no accounting-policy excerpts were retrieved for these variances)"
    return "\n\n".join(
        f'<excerpt citation="{r.chunk.citation}">\n{r.chunk.text}\n</excerpt>'
        for r in chunks
    )


def build_user_prompt(flagged: list[LineVariance], chunks: list[SearchResult]) -> str:
    rows = []
    for lv in flagged:
        rows.append(
            f"- account {lv.account} | {lv.line_item} | category: {lv.category or '(none)'}\n"
            f"  budget {lv.budget_amount} {lv.currency}, actual {lv.actual_amount} {lv.currency}, "
            f"variance {lv.variance} ({_pct_text(lv)}), {lv.direction.replace('_', ' ')}\n"
            f"  flagged because: {'; '.join(lv.flag_reasons)}"
        )
    figures = "\n".join(rows)
    return (
        f"<context>\n{_format_context(chunks)}\n</context>\n\n"
        f"Flagged variances for period {flagged[0].period}. These figures are final and "
        f"authoritative — do not recompute or dispute them:\n{figures}\n\n"
        "Call record_variance_explanations with one entry per flagged line above, "
        "matching each on its account and line_item exactly."
    )


@dataclass(frozen=True)
class ExplanationResult:
    explanations: Optional[list[VarianceExplanation]]  # None on refusal / parse failure
    model: str
    prompt_hash: str
    chunk_ids: list[str]
    citations: list[str]
    refused: bool = False
    refusal_category: Optional[str] = None
    parse_error: Optional[str] = None


def _parse_explanations(payload: dict, flagged: list[LineVariance]) -> list[VarianceExplanation]:
    raw = payload["explanations"]
    if not isinstance(raw, list):
        raise ValueError("record_variance_explanations returned no explanations list")

    valid_keys = {(lv.account, lv.line_item) for lv in flagged}
    by_key: dict[tuple[str, str], VarianceExplanation] = {}
    for entry in raw:
        key = (str(entry["account"]), str(entry["line_item"]))
        if key not in valid_keys:
            raise ValueError(f"explanation for unknown line {key}")
        citations = entry.get("citations") or []
        drivers = entry.get("primary_drivers") or []
        by_key[key] = VarianceExplanation(
            account=key[0],
            line_item=key[1],
            explanation=str(entry.get("explanation", "")),
            citations=[str(c) for c in citations],
            primary_drivers=[str(d) for d in drivers],
        )

    # One explanation per flagged line, in flagged order; fill any the model omitted.
    return [
        by_key.get(
            (lv.account, lv.line_item),
            VarianceExplanation(
                account=lv.account,
                line_item=lv.line_item,
                explanation="No explanation returned for this line.",
                citations=[],
                primary_drivers=[],
            ),
        )
        for lv in flagged
    ]


def draft_explanations(
    client,
    flagged: list[LineVariance],
    chunks: list[SearchResult],
    model: str = DEFAULT_MODEL,
    effort: str = DEFAULT_EFFORT,
) -> ExplanationResult:
    chunk_ids = [r.chunk.chunk_id for r in chunks]
    citations = [r.chunk.citation for r in chunks]

    user_prompt = build_user_prompt(flagged, chunks)
    prompt_hash = hashlib.sha256((SYSTEM_PROMPT + user_prompt).encode("utf-8")).hexdigest()

    response = client.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        output_config={"effort": effort},
        tools=[RECORD_EXPLANATIONS_TOOL],
        tool_choice={"type": "tool", "name": "record_variance_explanations"},
        messages=[{"role": "user", "content": user_prompt}],
    )

    base = dict(model=model, prompt_hash=prompt_hash, chunk_ids=chunk_ids, citations=citations)

    if response.stop_reason == "refusal":
        stop_details = getattr(response, "stop_details", None)
        category = getattr(stop_details, "category", None) if stop_details else None
        return ExplanationResult(explanations=None, refused=True, refusal_category=category, **base)

    tool_use = next(
        (b for b in response.content if getattr(b, "type", None) == "tool_use"),
        None,
    )
    if tool_use is None:
        return ExplanationResult(
            explanations=None,
            parse_error="no record_variance_explanations tool call in the response",
            **base,
        )

    try:
        explanations = _parse_explanations(tool_use.input, flagged)
    except (KeyError, TypeError, ValueError) as exc:
        return ExplanationResult(
            explanations=None,
            parse_error=f"could not parse record_variance_explanations output: {exc}",
            **base,
        )

    return ExplanationResult(explanations=explanations, **base)
