# technical-accounting-agent

Plain-English GAAP/IFRS Q&A, grounded strictly in `platform/knowledge`
chunks and drafted for human review — no free-standing model knowledge, no
final answer without an approver's sign-off.

## Scope
Given a question, retrieves relevant chunks from a pre-ingested
`platform/knowledge.KnowledgeBase`, asks the Claude API to draft an answer
using only those chunks (citing each one it relies on), and submits the
draft through `platform/approvals` for review. Out of scope for this
iteration: ingesting/curating the knowledge corpus itself (that's
`platform/knowledge`'s job — see its README for the "swap in real ASC/IFRS
excerpts" gap), multi-turn follow-up questions, and anything with a write
effect (this agent has no connectors and never will need write access — it
only ever produces text for a human to read).

## Tasks
| Task | Input | Output | Autonomy |
|---|---|---|---|
| answer_question | question, knowledge_base, top_k, model, effort | drafted, cited answer submitted for approval (`null` approval request on a safety refusal) | draft-only |

## Connectors required
None. Retrieval goes entirely through `platform/knowledge.KnowledgeBase`
(CLAUDE.md golden rule #1) — this agent never touches a document store,
ERP, or bank connector.

## Answering rules
1. Retrieve the top-k chunks for the question via `KnowledgeBase.search()`.
2. The system prompt instructs the model to answer **strictly** from those
   chunks, cite every claim by the chunk's `citation` label, and say so
   explicitly when the chunks don't contain enough to answer — never fill
   gaps from general training knowledge.
3. The drafted answer is submitted to `platform/approvals`
   (`autonomy: draft_only`) and is never treated as final until a
   reviewer and approver both sign off.
4. Every step — chunks retrieved, model/prompt-hash used to draft, the
   drafted text itself, and the approval submission — is written to
   `platform/audit-log`, so a run can be reconstructed as auditor evidence.

Retrieval and citation bookkeeping are deterministic code; the LLM's only
job is drafting the answer's language (CLAUDE.md golden rule #4).

## Model
Default model is `claude-sonnet-5` at `output_config.effort: "medium"`
(`technical_accounting_agent.llm.DEFAULT_MODEL` /`.DEFAULT_EFFORT`). This is
a deliberate cost tradeoff while the agent is new and still being debugged —
not a claim that Sonnet is the right long-term tier for GAAP/IFRS research.
Both are overridable per call via `answer_question(model=..., effort=...)`.

## Dependency: `anthropic` SDK
This is the first runtime dependency in the project — every other
`pyproject.toml` here has `dependencies = []`. It's scoped to this agent's
own `pyproject.toml` (not `platform/` or the repo root) because this is
currently the only module that calls the Claude API. The client reads
`ANTHROPIC_API_KEY` from the environment; it's never hardcoded and is only
ever constructed lazily (in `runner.answer_question` when no `client` is
passed, or in `manual_live_run.py`) — importing `technical_accounting_agent`
never requires the key or even a network connection.

## Evals
Golden test cases live in `evals/`, using a synthetic fixture corpus
(`evals/fixtures.py`, clearly labeled placeholder text — see its docstring)
and a fake Anthropic client (`evals/fakes.py`) so the automated suite never
makes a real API call or costs real tokens:

```bash
cd agents/technical-accounting-agent && ../../.venv/bin/pytest -v
```

### Manual live run
To see the agent make a real Claude API call end to end:

```bash
ANTHROPIC_API_KEY=sk-... python agents/technical-accounting-agent/manual_live_run.py
```

This is a standalone script (not collected by pytest, never run in CI) that
ingests the same synthetic fixture, asks one question, and prints the
retrieved chunks, the drafted answer with citations, and the resulting
approval request — plus the audit log's hash-chain verification.
