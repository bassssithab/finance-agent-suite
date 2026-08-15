"""Builds the strict-context prompt and calls the Claude API to draft a PBC
response. This is the only module that talks to the LLM (CLAUDE.md rule #4:
deterministic code does tie-out matching, the LLM only drafts language —
nothing here does matching or arithmetic).

`draft_response` takes an already-constructed client so callers/tests can
inject a fake one (see evals/test_llm.py, evals/test_end_to_end.py) instead
of monkeypatching the SDK. A real `anthropic.Anthropic()` client is only ever
constructed by runner.py (lazily, when no client is passed in) or
manual_live_run.py — never here, and never at import time — so importing
this module never requires ANTHROPIC_API_KEY or even the anthropic package.

Model default is claude-sonnet-5 at effort "medium": the same cost tradeoff
technical-accounting-agent makes while this agent is new and still being
debugged — see agents/audit-readiness-agent/README.md.
"""

import hashlib

from knowledge import SearchResult

from .models import PBCItem, PBCResponseDraft, TieOutResult

DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_EFFORT = "medium"
MAX_TOKENS = 2048

NO_EVIDENCE_MARKER = "NO_EVIDENCE_FOUND"

SYSTEM_PROMPT = """\
You are an audit-readiness assistant. You draft plain-English responses to \
"Prepared By Client" (PBC) requests from an external auditor, using ONLY the \
evidence and context excerpts provided below in <evidence> and <context> tags.

Rules:
1. Answer strictly from the provided evidence and context. Never use outside \
knowledge or assume evidence exists beyond what is given.
2. Every factual claim in your response must be supported by the evidence or \
context. Cite evidence by its audit event id exactly as given (e.g. \
"(reconciliation-agent audit event 3)") and cite context excerpts by their \
citation label exactly as given (e.g. "(Bank Reconciliation Policy (policy), \
chunk 1)").
3. If the evidence block says "NO_EVIDENCE_FOUND", say so explicitly in your \
response and flag this as an open item that requires human follow-up. Do not \
invent evidence or guess what the missing evidence would show.
4. This is a draft for a human reviewer, not a final position and not \
something to send to the auditor directly. Do not claim certainty beyond \
what the evidence supports.
"""


def _format_evidence(tie_out_result: TieOutResult) -> str:
    if not tie_out_result.found:
        return f"{NO_EVIDENCE_MARKER}\nReason: {tie_out_result.gap_reason}"

    blocks = []
    for entry in tie_out_result.entries:
        event_ids = ", ".join(f"audit event {eid}" for eid in entry.audit_event_ids)
        blocks.append(
            f'<entry source="{entry.evidence_agent}" cites="{event_ids}">\n'
            f"period: {entry.period_start} to {entry.period_end}\n"
            f"source_system: {entry.source_system}\n"
            f"approval_status: {entry.approval_status}\n"
            f"summary: {entry.summary}\n"
            "</entry>"
        )
    return "\n\n".join(blocks)


def _format_context(chunks: list[SearchResult]) -> str:
    blocks = [
        f'<excerpt citation="{r.chunk.citation}">\n{r.chunk.text}\n</excerpt>'
        for r in chunks
    ]
    return "\n\n".join(blocks)


def _evidence_citations(tie_out_result: TieOutResult) -> list[str]:
    citations = []
    for entry in tie_out_result.entries:
        for eid in entry.audit_event_ids:
            citations.append(f"{entry.evidence_agent} audit event {eid}")
    return citations


def build_user_prompt(
    pbc_item: PBCItem, tie_out_result: TieOutResult, knowledge_chunks: list[SearchResult],
) -> str:
    evidence = _format_evidence(tie_out_result)
    context = _format_context(knowledge_chunks)
    return (
        f"<evidence>\n{evidence}\n</evidence>\n\n"
        f"<context>\n{context}\n</context>\n\n"
        f"PBC request ({pbc_item.item_id}), period {pbc_item.period_start} to "
        f"{pbc_item.period_end}: {pbc_item.description}"
    )


def draft_response(
    client,
    pbc_item: PBCItem,
    tie_out_result: TieOutResult,
    knowledge_chunks: list[SearchResult],
    model: str = DEFAULT_MODEL,
    effort: str = DEFAULT_EFFORT,
) -> PBCResponseDraft:
    evidence_event_ids = [
        eid for entry in tie_out_result.entries for eid in entry.audit_event_ids
    ]
    knowledge_chunk_ids = [r.chunk.chunk_id for r in knowledge_chunks]
    citations = _evidence_citations(tie_out_result) + [r.chunk.citation for r in knowledge_chunks]

    user_prompt = build_user_prompt(pbc_item, tie_out_result, knowledge_chunks)
    prompt_hash = hashlib.sha256((SYSTEM_PROMPT + user_prompt).encode("utf-8")).hexdigest()

    response = client.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        output_config={"effort": effort},
        messages=[{"role": "user", "content": user_prompt}],
    )

    if response.stop_reason == "refusal":
        stop_details = getattr(response, "stop_details", None)
        category = getattr(stop_details, "category", None) if stop_details else None
        return PBCResponseDraft(
            item_id=pbc_item.item_id,
            model=model,
            prompt_hash=prompt_hash,
            tie_out_found=tie_out_result.found,
            evidence_event_ids=evidence_event_ids,
            knowledge_chunk_ids=knowledge_chunk_ids,
            citations=citations,
            response_text=None,
            refused=True,
            refusal_category=category,
        )

    text = next((block.text for block in response.content if block.type == "text"), "")
    return PBCResponseDraft(
        item_id=pbc_item.item_id,
        model=model,
        prompt_hash=prompt_hash,
        tie_out_found=tie_out_result.found,
        evidence_event_ids=evidence_event_ids,
        knowledge_chunk_ids=knowledge_chunk_ids,
        citations=citations,
        response_text=text,
        refused=False,
        refusal_category=None,
    )
