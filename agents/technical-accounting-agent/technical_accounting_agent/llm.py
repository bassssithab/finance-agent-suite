"""Builds the strict-context prompt and calls the Claude API to draft an
answer. This is the only module that talks to the LLM (CLAUDE.md rule #4:
deterministic code does retrieval, the LLM only drafts language — nothing
here does arithmetic or matching).

`draft_answer` takes an already-constructed client so callers/tests can
inject a fake one (see evals/test_llm.py, evals/test_end_to_end.py) instead
of monkeypatching the SDK. A real `anthropic.Anthropic()` client is only ever
constructed by runner.py (lazily, when no client is passed in) or
manual_live_run.py — never here, and never at import time — so importing
this module never requires ANTHROPIC_API_KEY or even the anthropic package.

Model default is claude-sonnet-5 at effort "medium": a deliberate cost
tradeoff while this agent is new and still being debugged, not a claim that
Sonnet is generally the right tier for GAAP/IFRS research — see
agents/technical-accounting-agent/README.md.
"""

import hashlib

from knowledge import SearchResult

from .models import AnswerDraft

DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_EFFORT = "medium"
MAX_TOKENS = 2048

SYSTEM_PROMPT = """\
You are a technical accounting research assistant. You answer questions about \
GAAP/IFRS treatment using ONLY the excerpts provided below in <context> tags.

Rules:
1. Answer strictly from the provided context. Never use outside knowledge of \
accounting standards, even if you believe you know the answer.
2. Every claim in your answer must be supported by at least one of the \
provided excerpts. Cite the excerpt you relied on immediately after the claim \
it supports, using its citation label exactly as given, e.g. \
"(Lease Accounting Summary (standard), chunk 1)".
3. If the provided context does not contain enough information to answer the \
question, say so explicitly and do not guess. Do not fill gaps with general \
accounting knowledge.
4. This is a draft for a human reviewer, not a final position. Do not claim \
certainty beyond what the context supports.
"""


def _format_context(chunks: list[SearchResult]) -> str:
    blocks = [
        f'<excerpt citation="{r.chunk.citation}">\n{r.chunk.text}\n</excerpt>'
        for r in chunks
    ]
    return "\n\n".join(blocks)


def build_user_prompt(question: str, chunks: list[SearchResult]) -> str:
    context = _format_context(chunks)
    return f"<context>\n{context}\n</context>\n\nQuestion: {question}"


def draft_answer(
    client,
    question: str,
    chunks: list[SearchResult],
    model: str = DEFAULT_MODEL,
    effort: str = DEFAULT_EFFORT,
) -> AnswerDraft:
    citations = [r.chunk.citation for r in chunks]
    chunk_ids = [r.chunk.chunk_id for r in chunks]

    user_prompt = build_user_prompt(question, chunks)
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
        return AnswerDraft(
            question=question,
            model=model,
            prompt_hash=prompt_hash,
            chunk_ids=chunk_ids,
            citations=citations,
            answer_text=None,
            refused=True,
            refusal_category=category,
        )

    text = next((block.text for block in response.content if block.type == "text"), "")
    return AnswerDraft(
        question=question,
        model=model,
        prompt_hash=prompt_hash,
        chunk_ids=chunk_ids,
        citations=citations,
        answer_text=text,
        refused=False,
        refusal_category=None,
    )
