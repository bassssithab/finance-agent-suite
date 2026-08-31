"""Drafts a plain-English deficiency narrative for each flagged journal entry
via one Claude API call, grounded in retrieved `platform/knowledge`
internal-controls-policy chunks when any are available.

The only module that talks to the LLM. CLAUDE.md rule #4: the model writes prose
only — it is handed the deterministic segregation-of-duties test result and told
it is authoritative; it never re-decides whether the control failed and does no
arithmetic. Citations are mandatory when context is used
(docs/ARCHITECTURE.md): a narrative grounded in an excerpt must name it; an
ungrounded narrative (no excerpts retrieved) comes back with an empty citations
list and must not invent a control framework or policy.

`draft_deficiency_narratives` takes an already-constructed client so
callers/tests can inject a fake one — a real `anthropic.Anthropic()` is only
ever built by runner.py (lazily, and only when there is at least one violation
to narrate) or manual_live_run.py. Importing this module never needs
ANTHROPIC_API_KEY.

Model default is claude-sonnet-5 at effort "medium": the same deliberate cost
tradeoff as agents/close-agent, agents/ap-agent and agents/vat-treatment-agent
while this agent is new — see agents/controls-sox-agent/README.md.
"""

import hashlib
from dataclasses import dataclass
from typing import Optional

from knowledge import SearchResult

from .models import ControlTestResult, DeficiencyNarrative, Violation

DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_EFFORT = "medium"
MAX_TOKENS = 2048

# One (result, violation) pair per flagged exception — an entry with two
# exceptions appears twice, once per code.
FlaggedPair = tuple[ControlTestResult, Violation]

RECORD_NARRATIVES_TOOL = {
    "name": "record_deficiency_narratives",
    "description": (
        "Record a plain-English control-deficiency narrative for each flagged "
        "journal-entry segregation-of-duties exception."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "narratives": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "entry_id": {"type": "string", "description": "Entry id of the flagged exception, exactly as given."},
                        "violation_code": {"type": "string", "description": "Violation code of the flagged exception, exactly as given."},
                        "narrative": {"type": "string", "description": "2-4 plain sentences describing the control deficiency for a SOX deficiency log / controls workpaper."},
                        "citations": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Citation labels of the excerpts relied on, exactly as given. Empty when no context was provided or used.",
                        },
                        "remediation": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Short phrases naming possible remediation steps, where identifiable. May be empty.",
                        },
                    },
                    "required": ["entry_id", "violation_code", "narrative", "citations", "remediation"],
                },
            },
        },
        "required": ["narratives"],
    },
}

SYSTEM_PROMPT = """\
You are an internal-controls (SOX) assistant. A control tester has run the \
segregation-of-duties control over journal-entry approvals and flagged a set of \
entries. For each flagged exception, draft a short plain-English deficiency \
narrative for the controls workpaper.

Rules:
1. Write 2-4 sentences per exception, in plain language a reviewer can drop \
straight into a SOX deficiency log.
2. The test result you are given is final and authoritative. The entry was \
flagged by a deterministic rule — never dispute that it failed, never re-argue \
the preparer/approver comparison, and never do arithmetic of your own.
3. When <context> contains internal-controls-policy excerpts, ground your \
narrative in them and cite the excerpt you relied on using its citation label \
exactly as given. When no excerpts are provided, describe the deficiency in \
general internal-control terms and do NOT assert a specific company policy or a \
specific framework (COSO, PCAOB AS 2201) — say plainly when a definitive \
reference would need information you do not have.
4. Do not classify the deficiency's severity — do not call it a material \
weakness, a significant deficiency, or merely a deficiency, and do not conclude \
it is immaterial, acceptable, or a one-off. Severity and aggregation are the \
reviewer's and the external auditor's judgement, not yours.
5. This is a draft for human review, not a final controls conclusion.
Always respond by calling the record_deficiency_narratives tool.
"""


def _fmt_approvers(result: ControlTestResult) -> str:
    parts = [f"approver_1={result.approver_1 or '(none)'}"]
    if result.approver_2 is not None:
        parts.append(f"approver_2={result.approver_2}")
    return ", ".join(parts)


def describe_violation(result: ControlTestResult, violation: Violation) -> str:
    """Deterministic natural-language description of one flagged exception — used
    as the platform/knowledge search query (CLAUDE.md rule #4: plain code builds
    the query, the LLM only writes prose from what it is handed)."""
    return (
        f"Journal entry {result.entry_id} for account {result.account}, amount "
        f"{result.amount} {result.currency}. Segregation-of-duties control exception "
        f"({violation.code}): {violation.detail}. What internal-control policy or "
        "guidance applies to journal-entry preparation and approval, segregation of "
        "duties, dual approval thresholds, and documenting this kind of control "
        "deficiency?"
    )


def _format_context(chunks: list[SearchResult]) -> str:
    if not chunks:
        return "(no internal-controls-policy excerpts were retrieved for these exceptions)"
    return "\n\n".join(
        f'<excerpt citation="{r.chunk.citation}">\n{r.chunk.text}\n</excerpt>'
        for r in chunks
    )


def build_user_prompt(flagged: list[FlaggedPair], chunks: list[SearchResult]) -> str:
    rows = []
    for result, violation in flagged:
        rows.append(
            f"- entry {result.entry_id} | account {result.account} | "
            f"amount {result.amount} {result.currency} | preparer {result.preparer} | "
            f"{_fmt_approvers(result)} | "
            f"dual approval required: {'yes' if result.dual_approval_required else 'no'}\n"
            f"  exception {violation.code}: {violation.detail}"
        )
    exceptions = "\n".join(rows)
    return (
        f"<context>\n{_format_context(chunks)}\n</context>\n\n"
        "Flagged segregation-of-duties exceptions. Each was flagged by a "
        "deterministic rule and the finding is final — do not dispute it:\n"
        f"{exceptions}\n\n"
        "Call record_deficiency_narratives with one entry per exception above, "
        "matching each on its entry_id and violation_code exactly."
    )


@dataclass(frozen=True)
class NarrativeResult:
    narratives: Optional[list[DeficiencyNarrative]]  # None on refusal / parse failure
    model: str
    prompt_hash: str
    chunk_ids: list[str]
    citations: list[str]
    refused: bool = False
    refusal_category: Optional[str] = None
    parse_error: Optional[str] = None


def _parse_narratives(payload: dict, flagged: list[FlaggedPair]) -> list[DeficiencyNarrative]:
    raw = payload["narratives"]
    if not isinstance(raw, list):
        raise ValueError("record_deficiency_narratives returned no narratives list")

    valid_keys = {(r.entry_id, v.code) for r, v in flagged}
    by_key: dict[tuple[str, str], DeficiencyNarrative] = {}
    for entry in raw:
        key = (str(entry["entry_id"]), str(entry["violation_code"]))
        if key not in valid_keys:
            raise ValueError(f"narrative for unknown exception {key}")
        citations = entry.get("citations") or []
        remediation = entry.get("remediation") or []
        by_key[key] = DeficiencyNarrative(
            entry_id=key[0],
            violation_code=key[1],
            narrative=str(entry.get("narrative", "")),
            citations=[str(c) for c in citations],
            remediation=[str(r) for r in remediation],
        )

    # One narrative per flagged exception, in flagged order; fill any the model omitted.
    return [
        by_key.get(
            (result.entry_id, violation.code),
            DeficiencyNarrative(
                entry_id=result.entry_id,
                violation_code=violation.code,
                narrative="No narrative returned for this exception.",
                citations=[],
                remediation=[],
            ),
        )
        for result, violation in flagged
    ]


def draft_deficiency_narratives(
    client,
    flagged: list[FlaggedPair],
    chunks: list[SearchResult],
    model: str = DEFAULT_MODEL,
    effort: str = DEFAULT_EFFORT,
) -> NarrativeResult:
    chunk_ids = [r.chunk.chunk_id for r in chunks]
    citations = [r.chunk.citation for r in chunks]

    user_prompt = build_user_prompt(flagged, chunks)
    prompt_hash = hashlib.sha256((SYSTEM_PROMPT + user_prompt).encode("utf-8")).hexdigest()

    response = client.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        output_config={"effort": effort},
        tools=[RECORD_NARRATIVES_TOOL],
        tool_choice={"type": "tool", "name": "record_deficiency_narratives"},
        messages=[{"role": "user", "content": user_prompt}],
    )

    base = dict(model=model, prompt_hash=prompt_hash, chunk_ids=chunk_ids, citations=citations)

    if response.stop_reason == "refusal":
        stop_details = getattr(response, "stop_details", None)
        category = getattr(stop_details, "category", None) if stop_details else None
        return NarrativeResult(narratives=None, refused=True, refusal_category=category, **base)

    tool_use = next(
        (b for b in response.content if getattr(b, "type", None) == "tool_use"),
        None,
    )
    if tool_use is None:
        return NarrativeResult(
            narratives=None,
            parse_error="no record_deficiency_narratives tool call in the response",
            **base,
        )

    try:
        narratives = _parse_narratives(tool_use.input, flagged)
    except (KeyError, TypeError, ValueError) as exc:
        return NarrativeResult(
            narratives=None,
            parse_error=f"could not parse record_deficiency_narratives output: {exc}",
            **base,
        )

    return NarrativeResult(narratives=narratives, **base)
