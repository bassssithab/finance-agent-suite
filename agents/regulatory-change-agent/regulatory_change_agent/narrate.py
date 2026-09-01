"""Drafts the plain-English impact-assessment narrative for a regulatory-change
triage via one Claude API call, grounded in retrieved `platform/knowledge`
regulatory-guidance chunks when any are available.

The only module that talks to the LLM. CLAUDE.md rule #4: the model writes prose
only — it is handed the deterministic keyword/category triage output and told it
is the triage's result; it does no matching or scoring of its own.

Overconfidence is the specific risk this module guards against. A keyword
overlap is NOT legal analysis, and this agent must never conclude the company is
or is not compliant. The system prompt below forbids any compliance conclusion,
requires the model to state — twice — that a qualified legal/compliance
professional must review the requirement regardless of what the triage found,
and requires it to describe a keyword match as "appears relevant", not
"addresses". Citations are mandatory when context is used
(docs/ARCHITECTURE.md); an ungrounded narrative must not assert what any
specific regulation requires.

`draft_impact_assessment` takes an already-constructed client so callers/tests
can inject a fake one — a real `anthropic.Anthropic()` is only ever built by
runner.py (lazily) or manual_live_run.py. Importing this module never needs
ANTHROPIC_API_KEY.

Model default is claude-sonnet-5 at effort "medium": the same deliberate cost
tradeoff as agents/close-agent, agents/fpa-agent and agents/tax-compliance-agent
while this agent is new — see agents/regulatory-change-agent/README.md.
"""

import hashlib
from dataclasses import dataclass
from typing import Optional

from knowledge import SearchResult

from .models import ControlRelevance, ImpactNarrative

DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_EFFORT = "medium"
MAX_TOKENS = 2048

RECORD_IMPACT_ASSESSMENT_TOOL = {
    "name": "record_impact_assessment",
    "description": (
        "Record the plain-English first-pass impact-assessment narrative for a "
        "regulatory-change triage."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "assessment": {
                "type": "string",
                "description": "3-6 sentences: what the keyword triage surfaced and what a reviewer should examine. Never a compliance conclusion. Must itself say that qualified legal/compliance review is required regardless.",
            },
            "relevant_controls_explained": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "control_id": {"type": "string", "description": "Control id, exactly as given."},
                        "explanation": {"type": "string", "description": "Which terms overlapped and why the control APPEARS relevant - noting that appearing relevant on keywords is not the same as addressing the requirement."},
                    },
                    "required": ["control_id", "explanation"],
                },
                "description": "One entry per surfaced control. Empty list when none were surfaced.",
            },
            "gap_explanation": {
                "type": ["string", "null"],
                "description": "When a gap or weak coverage was flagged: explain what the triage did and did not find, and that it does not confirm a gap exists. Null when nothing was flagged.",
            },
            "review_required_statement": {
                "type": "string",
                "description": "A plain statement that a qualified legal and/or compliance professional must review this requirement against the controls regardless of what the triage found.",
            },
            "citations": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Citation labels of the excerpts relied on, exactly as given. Empty when no context was provided or used.",
            },
        },
        "required": [
            "assessment", "relevant_controls_explained", "gap_explanation",
            "review_required_statement", "citations",
        ],
    },
}

SYSTEM_PROMPT = """\
You are assisting with a FIRST-PASS regulatory-change triage. Simple code has \
compared a new or changed regulatory requirement against the company's \
internal-controls register by keyword and category overlap, and has produced a \
shortlist of controls that MIGHT be relevant and a coverage verdict. Draft the \
narrative for a reviewer.

Rules:
1. This is a first-pass triage, NOT a legal or compliance determination. The \
keyword overlap you are handed is a crude relevance signal from simple code - \
it is not legal analysis. A keyword match does not mean a control satisfies the \
requirement, and the absence of a match does not prove a real gap.
2. Never state or imply that the company is compliant, non-compliant, covered, \
exposed, at risk, in breach, or that the requirement "is addressed" or "a gap \
exists" as a conclusion. Do not write "no action needed", "the company meets \
this", "this is handled", "there is a gap", "the requirement is satisfied". \
Describe only what the triage surfaced and what a reviewer should examine.
3. State plainly - in the assessment AND in review_required_statement - that a \
qualified legal and/or compliance professional must review this requirement \
against the controls regardless of what the triage found. The tool does not and \
cannot make that call.
4. The control list, the overlap scores, the matched terms and the coverage \
verdict are the triage's output. Do not recompute or dispute them; explain what \
a reviewer should look at.
5. For each surfaced control, name the terms that overlapped and say that the \
control APPEARS potentially relevant - and that appearing relevant on keywords \
is not the same as actually addressing the requirement.
6. When <context> contains regulatory-guidance excerpts, ground the assessment \
in them and cite the excerpt you relied on using its citation label exactly as \
given. When no excerpts are provided, do NOT assert what any specific \
regulation requires or what its deadlines or thresholds are.
7. This is a draft for human review.
Always respond by calling the record_impact_assessment tool.
"""


def describe_requirement(requirement_text: str, verdict: str) -> str:
    """Deterministic natural-language search query for the requirement — plain
    code builds the query, the LLM only writes prose from what it is handed."""
    return (
        f"New or changed regulatory requirement: {requirement_text} "
        f"First-pass keyword triage verdict: {verdict}. "
        "What regulatory-change management or control-mapping guidance applies to "
        "triaging a new requirement against existing controls, confirming a suspected "
        "gap, and the role of legal and compliance review?"
    )


def describe_control(control: ControlRelevance) -> str:
    """Deterministic query for one surfaced control's subject area."""
    return (
        f"Internal control {control.control_id} ({control.category or 'uncategorised'}): "
        f"{control.description}. What guidance applies to mapping this kind of control "
        "to a regulatory obligation?"
    )


def _format_context(chunks: list[SearchResult]) -> str:
    if not chunks:
        return "(no regulatory-guidance excerpts were retrieved for this triage)"
    return "\n\n".join(
        f'<excerpt citation="{r.chunk.citation}">\n{r.chunk.text}\n</excerpt>'
        for r in chunks
    )


def build_user_prompt(
    requirement_text: str,
    requirement_reference: Optional[str],
    policy: dict,
    surfaced: list,
    coverage_verdict: str,
    gap_flagged: bool,
    flag_reasons: list,
    control_count: int,
    chunks: list,
) -> str:
    if surfaced:
        control_rows = "\n".join(
            f"- {cr.control_id} ({cr.category or 'uncategorised'}): "
            f"score {cr.score}, matched terms [{', '.join(cr.matched_terms)}]"
            f"{', category match' if cr.category_match else ''}\n"
            f"  description: {cr.description}"
            for cr in surfaced
        )
    else:
        control_rows = "(no control was surfaced as apparently relevant)"

    reasons = "\n".join(f"- {r}" for r in flag_reasons) or "(none)"
    ref = f" (reference {requirement_reference})" if requirement_reference else ""

    return (
        f"<context>\n{_format_context(chunks)}\n</context>\n\n"
        f"Regulatory requirement{ref}:\n{requirement_text}\n\n"
        f"First-pass keyword/category triage against {control_count} internal "
        f"controls (min overlap {policy.get('min_keyword_overlap')} terms, solid-match "
        f"bar {policy.get('strong_overlap')} terms).\n"
        f"Coverage verdict: {coverage_verdict}. Gap flagged for review: {gap_flagged}.\n"
        f"Flag reasons:\n{reasons}\n\n"
        f"Surfaced controls (the triage's shortlist — appearing relevant on keywords, "
        f"NOT confirmed to address the requirement):\n{control_rows}\n\n"
        "Call record_impact_assessment. Do not conclude on compliance; state that "
        "qualified legal/compliance review is required regardless; explain each "
        "surfaced control's apparent relevance and any flagged gap."
    )


@dataclass(frozen=True)
class NarrativeResult:
    narrative: Optional[ImpactNarrative]  # None on refusal / parse failure
    model: str
    prompt_hash: str
    chunk_ids: list
    citations: list
    refused: bool = False
    refusal_category: Optional[str] = None
    parse_error: Optional[str] = None


def _parse_narrative(payload: dict) -> ImpactNarrative:
    assessment = payload["assessment"]
    if not isinstance(assessment, str) or not assessment.strip():
        raise ValueError("record_impact_assessment returned an empty assessment")

    review_stmt = payload["review_required_statement"]
    if not isinstance(review_stmt, str) or not review_stmt.strip():
        raise ValueError("record_impact_assessment returned an empty review_required_statement")

    raw_controls = payload.get("relevant_controls_explained")
    if raw_controls is None:
        raw_controls = []
    if not isinstance(raw_controls, list):
        raise ValueError("relevant_controls_explained must be a list")
    explained = [
        {"control_id": str(e["control_id"]), "explanation": str(e.get("explanation", ""))}
        for e in raw_controls
    ]

    gap = payload.get("gap_explanation")
    if gap is not None and not isinstance(gap, str):
        raise ValueError("gap_explanation must be a string or null")

    citations = payload.get("citations") or []
    if not isinstance(citations, list):
        raise ValueError("citations must be a list")

    return ImpactNarrative(
        assessment=str(assessment),
        relevant_controls_explained=explained,
        gap_explanation=str(gap) if gap else None,
        review_required_statement=str(review_stmt),
        citations=[str(c) for c in citations],
    )


def draft_impact_assessment(
    client,
    *,
    requirement_text: str,
    requirement_reference: Optional[str],
    policy: dict,
    surfaced: list,
    coverage_verdict: str,
    gap_flagged: bool,
    flag_reasons: list,
    control_count: int,
    chunks: list,
    model: str = DEFAULT_MODEL,
    effort: str = DEFAULT_EFFORT,
) -> NarrativeResult:
    chunk_ids = [r.chunk.chunk_id for r in chunks]
    citations = [r.chunk.citation for r in chunks]

    user_prompt = build_user_prompt(
        requirement_text, requirement_reference, policy, surfaced,
        coverage_verdict, gap_flagged, flag_reasons, control_count, chunks,
    )
    prompt_hash = hashlib.sha256((SYSTEM_PROMPT + user_prompt).encode("utf-8")).hexdigest()

    response = client.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        output_config={"effort": effort},
        tools=[RECORD_IMPACT_ASSESSMENT_TOOL],
        tool_choice={"type": "tool", "name": "record_impact_assessment"},
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
            parse_error="no record_impact_assessment tool call in the response",
            **base,
        )

    try:
        narrative = _parse_narrative(tool_use.input)
    except (KeyError, TypeError, ValueError) as exc:
        return NarrativeResult(
            narrative=None,
            parse_error=f"could not parse record_impact_assessment output: {exc}",
            **base,
        )

    return NarrativeResult(narrative=narrative, **base)
