"""Drafts the plain-English forecast narrative via one Claude API call, grounded
in retrieved `platform/knowledge` FP&A-methodology chunks when any are
available.

The only module that talks to the LLM. CLAUDE.md rule #4: the model writes prose
only — it is handed the already-computed projected figures, growth rates, base
period and flagged items and told they are authoritative; it does no arithmetic.

Overconfidence is the specific risk this module guards against. A forecast is a
forward-looking projection built on assumptions, not a prediction. The system
prompt below forbids the model from presenting any projected figure as a
certainty or a guarantee, and requires it to describe every growth rate as an
assumption the planner supplied — not as a measured trend or a fact. Citations
are mandatory when context is used (docs/ARCHITECTURE.md); an ungrounded
narrative (no excerpts retrieved) comes back with an empty citations list and
must not invent a methodology or a company policy.

`draft_forecast_narrative` takes an already-constructed client so callers/tests
can inject a fake one — a real `anthropic.Anthropic()` is only ever built by
runner.py (lazily) or manual_live_run.py. Importing this module never needs
ANTHROPIC_API_KEY.

Model default is claude-sonnet-5 at effort "medium": the same deliberate cost
tradeoff as agents/close-agent and agents/ar-collections-agent while this agent
is new — see agents/fpa-agent/README.md.
"""

import hashlib
from dataclasses import dataclass
from typing import Optional

from knowledge import SearchResult

from .models import ForecastNarrative

DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_EFFORT = "medium"
MAX_TOKENS = 2048

RECORD_FORECAST_NARRATIVE_TOOL = {
    "name": "record_forecast_narrative",
    "description": "Record the plain-English narrative for a driver-based forecast.",
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "3-6 sentences on the overall projected trajectory. Written as a projection under stated assumptions, never as a prediction of what will happen.",
            },
            "assumptions_described": {
                "type": "array",
                "items": {"type": "string"},
                "description": "One entry per key growth assumption, each restated plainly AS an assumption (e.g. 'the plan assumes 3% monthly growth in Revenue').",
            },
            "flagged_items_called_out": {
                "type": "array",
                "items": {"type": "string"},
                "description": "One entry per flagged high-sensitivity line: name it and say a small change in its assumption moves the forecast materially, so it needs the reviewer's scrutiny. Empty list only when nothing was flagged.",
            },
            "citations": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Citation labels of the excerpts relied on, exactly as given. Empty when no context was provided or used.",
            },
        },
        "required": ["summary", "assumptions_described", "flagged_items_called_out", "citations"],
    },
}

SYSTEM_PROMPT = """\
You are an FP&A assistant. A planner has run a driver-based forecast: historical \
actuals projected forward using a set of growth assumptions. Draft the narrative \
for the planning pack.

Rules:
1. A forecast is a forward-looking projection built on assumptions - it is NOT a \
prediction, a guarantee, a commitment, or a statement of what will happen. Never \
write that a projected figure "will" be reached. Use "projected", "under these \
assumptions", "if the assumed growth rates hold". Do not attach confidence \
levels, probabilities, or likelihoods that you were not given.
2. Every growth rate is an assumption the planner supplied, not a trend you have \
measured or verified. Describe each one as an assumption - "the plan assumes X% \
growth per period in ..." - never as an established fact, a known trajectory, or \
something the data proves.
3. The projected figures, growth rates, base period, projected periods and \
flagged items you are given are final and authoritative. Never recompute, \
adjust, restate, or dispute them, and never do arithmetic of your own.
4. Call out EVERY flagged high-sensitivity line by name. For each, say plainly \
that because its assumed rate is large, a small change in that assumption moves \
the forecast materially, so it needs the reviewer's scrutiny. Do not reassure \
the reader that a flagged item is fine.
5. When <context> contains FP&A methodology or policy excerpts, ground your \
narrative in them and cite the excerpt you relied on using its citation label \
exactly as given. When no excerpts are provided, describe the forecast in \
general terms and do NOT assert a specific company methodology, policy, or \
review process.
6. Do not recommend a decision, approve or endorse the plan, or conclude that \
the forecast is reasonable, achievable, conservative, or aggressive - that \
judgement belongs to the human reviewer, not to you.
7. This is a draft for human review, not a final plan.
Always respond by calling the record_forecast_narrative tool.
"""


def describe_forecast(summary: dict, assumptions: dict) -> str:
    """Deterministic natural-language description of the whole forecast — used as
    a platform/knowledge search query (CLAUDE.md rule #4: plain code builds the
    query, the LLM only writes prose from what it is handed)."""
    categories = ", ".join(sorted(summary.get("by_category", {}))) or "several categories"
    return (
        f"Driver-based financial forecast: {summary.get('line_count', 0)} line items across "
        f"{categories}, projected {len(summary.get('projected_periods', []))} periods forward "
        f"from base period {summary.get('base_period')}. Default growth assumption "
        f"{assumptions.get('default_growth')} per period. "
        "What FP&A methodology or policy applies to building a driver-based forecast, "
        "documenting and owning growth assumptions, reviewing high-sensitivity "
        "assumptions, and presenting a forecast as a projection rather than a commitment?"
    )


def describe_flagged_line(flagged_line: dict) -> str:
    """Deterministic query for one flagged line."""
    return (
        f"Forecast line {flagged_line.get('account')} {flagged_line.get('line_item')} "
        f"(category {flagged_line.get('category')}), assumed growth rate "
        f"{flagged_line.get('growth_rate')} per period, flagged: "
        f"{'; '.join(flagged_line.get('flag_reasons', []))}. "
        "What FP&A guidance applies to a forecast assumption this sensitive?"
    )


def _format_context(chunks: list[SearchResult]) -> str:
    if not chunks:
        return "(no FP&A-methodology excerpts were retrieved for this forecast)"
    return "\n\n".join(
        f'<excerpt citation="{r.chunk.citation}">\n{r.chunk.text}\n</excerpt>'
        for r in chunks
    )


def _fmt_assumptions(assumptions: dict) -> str:
    rows = [f"- default growth: {assumptions.get('default_growth')} per period (applied to any line without a category rate)"]
    for category, rate in sorted((assumptions.get("category_growth") or {}).items()):
        rows.append(f"- {category}: {rate} per period (category assumption)")
    rows.append(
        f"- sensitivity threshold: a line whose |assumed rate| >= "
        f"{assumptions.get('max_pop_change_pct')} is flagged for scrutiny"
    )
    return "\n".join(rows)


def build_user_prompt(
    summary: dict, assumptions: dict, flagged_lines: list, chunks: list
) -> str:
    by_cat = "\n".join(
        f"  {cat}: base {v['base']} -> projected {v['final']} "
        f"({v['growth_pct_over_horizon'] or 'n/a'} over the horizon)"
        for cat, v in summary.get("by_category", {}).items()
    )
    if flagged_lines:
        flagged = "\n".join(
            f"- {fl['account']} {fl['line_item']} (category {fl['category']}): assumed rate "
            f"{fl['growth_rate']} per period, projected {fl['projected_amount']} for {fl['period']}\n"
            f"  flagged because: {'; '.join(fl['flag_reasons'])}"
            for fl in flagged_lines
        )
    else:
        flagged = "(nothing was flagged as high-sensitivity)"

    return (
        f"<context>\n{_format_context(chunks)}\n</context>\n\n"
        f"Driver-based forecast. All figures below are final and authoritative — "
        f"do not recompute or dispute them.\n\n"
        f"Base period: {summary.get('base_period')}. Projected periods: "
        f"{', '.join(summary.get('projected_periods', []))}.\n"
        f"Total across all lines: base {summary.get('total_base')} -> projected "
        f"{summary.get('total_projected_final')} "
        f"({summary.get('total_growth_pct_over_horizon') or 'n/a'} over the horizon).\n"
        f"By category:\n{by_cat}\n\n"
        f"Assumptions (each supplied by the planner):\n{_fmt_assumptions(assumptions)}\n\n"
        f"Flagged high-sensitivity lines:\n{flagged}\n\n"
        "Call record_forecast_narrative. Restate each assumption AS an assumption, "
        "keep the language projective (never 'will'), and call out every flagged line."
    )


@dataclass(frozen=True)
class NarrativeResult:
    narrative: Optional[ForecastNarrative]  # None on refusal / parse failure
    model: str
    prompt_hash: str
    chunk_ids: list
    citations: list
    refused: bool = False
    refusal_category: Optional[str] = None
    parse_error: Optional[str] = None


def _parse_narrative(payload: dict) -> ForecastNarrative:
    summary = payload["summary"]
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("record_forecast_narrative returned an empty summary")

    def _str_list(value, name: str) -> list:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError(f"{name} must be a list")
        return [str(v) for v in value]

    return ForecastNarrative(
        summary=str(summary),
        assumptions_described=_str_list(payload.get("assumptions_described"), "assumptions_described"),
        flagged_items_called_out=_str_list(payload.get("flagged_items_called_out"), "flagged_items_called_out"),
        citations=_str_list(payload.get("citations"), "citations"),
    )


def draft_forecast_narrative(
    client,
    summary: dict,
    assumptions: dict,
    flagged_lines: list,
    chunks: list,
    model: str = DEFAULT_MODEL,
    effort: str = DEFAULT_EFFORT,
) -> NarrativeResult:
    chunk_ids = [r.chunk.chunk_id for r in chunks]
    citations = [r.chunk.citation for r in chunks]

    user_prompt = build_user_prompt(summary, assumptions, flagged_lines, chunks)
    prompt_hash = hashlib.sha256((SYSTEM_PROMPT + user_prompt).encode("utf-8")).hexdigest()

    response = client.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        output_config={"effort": effort},
        tools=[RECORD_FORECAST_NARRATIVE_TOOL],
        tool_choice={"type": "tool", "name": "record_forecast_narrative"},
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
            parse_error="no record_forecast_narrative tool call in the response",
            **base,
        )

    try:
        narrative = _parse_narrative(tool_use.input)
    except (KeyError, TypeError, ValueError) as exc:
        return NarrativeResult(
            narrative=None,
            parse_error=f"could not parse record_forecast_narrative output: {exc}",
            **base,
        )

    return NarrativeResult(narrative=narrative, **base)
