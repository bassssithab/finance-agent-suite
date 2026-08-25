# vat-treatment-agent

Classifies the VAT treatment of an invoice line item — standard-rated,
zero-rated export, exempt, or out-of-scope — grounded strictly in
`platform/knowledge` chunks and drafted for human review — no free-standing
model knowledge, no final answer without an approver's sign-off.

## Scope
Given a structured invoice line item (goods type, customer location,
transaction type), builds a deterministic description of it, retrieves
relevant chunks from a pre-ingested `platform/knowledge.KnowledgeBase`, asks
the Claude API to draft a classification and explanation using only those
chunks (citing each one it relies on), and submits the draft through
`platform/approvals` for review.

Out of scope for this iteration:
- Ingesting/curating the real VAT knowledge corpus (that's
  `platform/knowledge`'s job — see its README). The bundled eval corpus
  (`evals/fixtures.py`) is entirely fictional: a made-up jurisdiction
  ("Larenthia") and VAT code, not modeled on any real country or company.
- A dedicated example document for the "exempt" category. The corpus
  defines exempt in its general-scope overview but has no worked example, so
  a line item that should classify as exempt will correctly retrieve only
  that general definition — the strict-context system prompt is expected to
  make the model say the context isn't specific enough to classify
  confidently rather than guess (see `evals/test_end_to_end.py`'s
  `test_a_line_item_with_no_dedicated_exempt_document_still_drafts_and_submits`).
- Multi-line invoices, rate calculations, or filings — this agent classifies
  one line item's treatment and explains why; it never computes VAT amounts
  or files anything.
- Anything with a write effect — this agent has no connectors and never will
  need write access, it only ever produces text for a human to read.

## Tasks
| Task | Input | Output | Autonomy |
|---|---|---|---|
| determine_vat_treatment | line_item (goods_type, customer_location, transaction_type), knowledge_base, top_k, model, effort | drafted, cited VAT classification submitted for approval (`null` approval request on a safety refusal) | draft-only |

## Connectors required
None. Retrieval goes entirely through `platform/knowledge.KnowledgeBase`
(CLAUDE.md golden rule #1) — this agent never touches a document store,
ERP, or bank connector.

## Answering rules
1. Turn the line item into one description string (deterministic code, see
   `vat_treatment_agent.models.describe_line_item`), used both as the
   `KnowledgeBase.search()` query and the prompt's question — the LLM never
   assembles facts about the line item itself, only classifies/explains from
   the retrieved chunks (CLAUDE.md golden rule #4).
2. The system prompt instructs the model to classify strictly as one of
   standard-rated / zero-rated export / exempt / out-of-scope, cite every
   claim by the chunk's `citation` label, never conflate exempt with
   out-of-scope (they're distinct legal categories in this corpus — see
   `evals/fixtures.py`'s scope-and-rates document), and say so explicitly
   when the chunks don't contain enough to classify confidently — never fill
   gaps from general VAT knowledge.
3. The drafted answer is submitted to `platform/approvals`
   (`autonomy: draft_only`) and is never treated as final until a reviewer
   and approver both sign off.
4. Every step — chunks retrieved, model/prompt-hash used to draft, the
   drafted text itself, and the approval submission — is written to
   `platform/audit-log`, so a run can be reconstructed as auditor evidence.

## Model
Default model is `claude-sonnet-5` at `output_config.effort: "medium"`
(`vat_treatment_agent.llm.DEFAULT_MODEL` / `.DEFAULT_EFFORT`), the same
deliberate cost tradeoff as `agents/technical-accounting-agent` while this
agent is new and still being debugged — not a claim that Sonnet is the right
long-term tier for VAT research. Both are overridable per call via
`determine_vat_treatment(model=..., effort=...)`.

## Dependency: `anthropic` SDK
Scoped to this agent's own `pyproject.toml` (not `platform/` or the repo
root), same as `agents/technical-accounting-agent` — CLAUDE.md rule #5, one
agent per folder. The client reads `ANTHROPIC_API_KEY` from the environment;
it's never hardcoded and is only ever constructed lazily (in
`runner.determine_vat_treatment` when no `client` is passed, or in
`manual_live_run.py`) — importing `vat_treatment_agent` never requires the
key or even a network connection.

## Evals
Golden test cases live in `evals/`, using a synthetic fixture corpus
(`evals/fixtures.py`, clearly labeled fictional — see its docstring) and a
fake Anthropic client (`evals/fakes.py`) so the automated suite never makes
a real API call or costs real tokens:

```bash
cd agents/vat-treatment-agent && ../../.venv/bin/pytest -v
```

### Manual live run
To see the agent make a real Claude API call end to end:

```bash
ANTHROPIC_API_KEY=sk-... python agents/vat-treatment-agent/manual_live_run.py
```

This is a standalone script (not collected by pytest, never run in CI) that
ingests the same synthetic fixture, classifies one line item, and prints the
retrieved chunks, the drafted answer with citations, and the resulting
approval request — plus the audit log's hash-chain verification.
