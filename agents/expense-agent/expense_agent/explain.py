"""Optional cited explanation: for each flagged policy violation, draft a short
plain-English note of which written expense-policy rule applies and why the
receipt breaches it, grounded strictly in retrieved `platform/knowledge`
expense-policy chunks.

The second (and last) module that talks to the LLM. CLAUDE.md rule #4: the model
explains only — it is handed the deterministic violation (code + detail) and
told it is authoritative; it never re-decides whether the receipt is compliant
and does no arithmetic. Citations are mandatory when context is used
(docs/ARCHITECTURE.md): an explanation grounded in an excerpt must name it; an
explanation with no supporting excerpt must say so and must not invent a policy,
a limit, or an approval step.

If retrieval finds nothing relevant, the runner skips this step entirely — it is
genuinely optional (see the task spec / README), the same graceful skip
`ap_agent.coding` uses.
"""

import hashlib
from dataclasses import dataclass
from typing import Optional

from knowledge import SearchResult

from .models import ExtractedReceipt, PolicyExplanation, Violation

DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_EFFORT = "medium"
MAX_TOKENS = 2048

RECORD_POLICY_EXPLANATIONS_TOOL = {
    "name": "record_policy_explanations",
    "description": (
        "Record a plain-English explanation for each flagged expense-policy "
        "violation, citing the policy excerpt relied on."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "explanations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "code": {"type": "string", "description": "Violation code of the flagged item, exactly as given."},
                        "explanation": {"type": "string", "description": "2-4 plain sentences: which policy rule applies and why this receipt breaches it, for an approver's file."},
                        "citations": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Citation labels of the excerpts relied on, exactly as given. Empty when no excerpt covers this violation.",
                        },
                    },
                    "required": ["code", "explanation", "citations"],
                },
            },
        },
        "required": ["explanations"],
    },
}

SYSTEM_PROMPT = """\
You are a travel-and-expense compliance assistant. A deterministic check has \
flagged a set of policy violations on one expense receipt. For each flagged \
violation, draft a short plain-English explanation for the approver's file, \
using ONLY the expense-policy excerpts provided in <context>.

Rules:
1. Write 2-4 sentences per violation: name the policy rule that applies and \
explain why this receipt breaches it.
2. The violation you are given was produced by a deterministic rule and is \
final. Never dispute that it was flagged, never re-argue the amount, the date, \
or the limit, and never do arithmetic of your own.
3. Ground every explanation in the provided excerpts and cite the excerpt you \
relied on using its citation label exactly as given. If no excerpt covers a \
violation, return an empty citations list and say plainly that the written \
policy on file does not cover this point - do NOT invent a rule, a limit, an \
interest charge, or an approval step.
4. Do not decide the outcome - whether to reimburse, reject, or escalate is the \
approver's call, not yours. This is a draft for human review.
Always respond by calling the record_policy_explanations tool.
"""


def describe_violation(violation: Violation, receipt: ExtractedReceipt) -> str:
    """Deterministic natural-language description of one flagged violation — used
    as the platform/knowledge search query (CLAUDE.md rule #4: plain code builds
    the query, the LLM only writes prose from what it is handed)."""
    return (
        f"Expense receipt from {receipt.vendor or '(unknown vendor)'}, category "
        f"{receipt.expense_category or '(uncategorised)'}, amount {receipt.amount} "
        f"{receipt.currency}. Expense-policy violation ({violation.code}): {violation.detail}. "
        "What expense-policy rule applies: spending limits by category, receipt "
        "submission deadlines and maximum age, required receipt information, and "
        "what happens to an out-of-policy claim?"
    )


def _format_context(chunks: list[SearchResult]) -> str:
    return "\n\n".join(
        f'<excerpt citation="{r.chunk.citation}">\n{r.chunk.text}\n</excerpt>'
        for r in chunks
    )


def build_user_prompt(
    receipt: ExtractedReceipt, violations: list[Violation], chunks: list[SearchResult]
) -> str:
    rows = "\n".join(
        f"- {v.code} (field: {v.field}): {v.detail}" for v in violations
    )
    return (
        f"<context>\n{_format_context(chunks)}\n</context>\n\n"
        f"Receipt: vendor {receipt.vendor or '(unknown)'}, date {receipt.date or '(none)'}, "
        f"amount {receipt.amount} {receipt.currency}, category "
        f"{receipt.expense_category or '(uncategorised)'}.\n\n"
        "Flagged policy violations. Each was produced by a deterministic rule and "
        f"is final - do not dispute it:\n{rows}\n\n"
        "Call record_policy_explanations with one entry per violation above, "
        "matching each on its code exactly."
    )


@dataclass(frozen=True)
class ExplanationResult:
    explanations: Optional[list[PolicyExplanation]]  # None on refusal / parse failure
    model: str
    prompt_hash: str
    chunk_ids: list[str]
    citations: list[str]
    refused: bool = False
    refusal_category: Optional[str] = None
    parse_error: Optional[str] = None


def _parse_explanations(payload: dict, violations: list[Violation]) -> list[PolicyExplanation]:
    raw = payload["explanations"]
    if not isinstance(raw, list):
        raise ValueError("record_policy_explanations returned no explanations list")

    valid_codes = {v.code for v in violations}
    by_code: dict[str, PolicyExplanation] = {}
    for entry in raw:
        code = str(entry["code"])
        if code not in valid_codes:
            raise ValueError(f"explanation for unknown violation code {code!r}")
        citations = entry.get("citations") or []
        by_code[code] = PolicyExplanation(
            code=code,
            explanation=str(entry.get("explanation", "")),
            citations=[str(c) for c in citations],
        )

    # One explanation per violation, in violation order; fill any the model omitted.
    return [
        by_code.get(
            v.code,
            PolicyExplanation(
                code=v.code,
                explanation="No explanation returned for this violation.",
                citations=[],
            ),
        )
        for v in violations
    ]


def draft_policy_explanations(
    client,
    receipt: ExtractedReceipt,
    violations: list[Violation],
    chunks: list[SearchResult],
    model: str = DEFAULT_MODEL,
    effort: str = DEFAULT_EFFORT,
) -> ExplanationResult:
    chunk_ids = [r.chunk.chunk_id for r in chunks]
    citations = [r.chunk.citation for r in chunks]

    user_prompt = build_user_prompt(receipt, violations, chunks)
    prompt_hash = hashlib.sha256((SYSTEM_PROMPT + user_prompt).encode("utf-8")).hexdigest()

    response = client.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        output_config={"effort": effort},
        tools=[RECORD_POLICY_EXPLANATIONS_TOOL],
        tool_choice={"type": "tool", "name": "record_policy_explanations"},
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
            parse_error="no record_policy_explanations tool call in the response",
            **base,
        )

    try:
        explanations = _parse_explanations(tool_use.input, violations)
    except (KeyError, TypeError, ValueError) as exc:
        return ExplanationResult(
            explanations=None,
            parse_error=f"could not parse record_policy_explanations output: {exc}",
            **base,
        )

    return ExplanationResult(explanations=explanations, **base)
