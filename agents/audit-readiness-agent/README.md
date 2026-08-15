# audit-readiness-agent

Auditee-side audit automation: PBC (Prepared-By-Client) request handling,
evidence collection, tie-out indexing, and cited response drafting for human
review. FLAGSHIP — see `docs/ROADMAP.md`.

## Status
First task-slice implemented: `respond_to_pbc_item`. It reuses
`reconciliation-agent`'s audit trail as its only evidence source and
`platform/knowledge` for supporting policy citations — proving the
"reuses recon outputs + knowledge layer + audit log" flagship story from
`docs/ROADMAP.md` end to end. Broader PBC intake (multiple items at once),
additional evidence types beyond bank reconciliations, and general-purpose
tie-out indexing across other agents' outputs are out of scope for this
iteration.

## Scope
Given a single structured PBC request (a specific period, evidence type, and
plain-English description of what the auditor is asking for), deterministically
finds matching evidence already recorded in a completed `reconciliation-agent`
run's audit log, drafts a plain-English response memo via the Claude API that
cites only that tied-out evidence (plus any relevant `platform/knowledge`
policy chunks), and submits the draft through `platform/approvals` for human
review before anything goes back to an auditor. If no matching evidence is
found, the draft says so explicitly and flags an open item — it never
fabricates evidence or an explanation the data doesn't support.

Out of scope for this iteration: ingesting/collecting evidence from live
document stores or ERPs (evidence here is entirely audit-log reuse, no new
connector), evidence types other than `bank_reconciliation`, batch handling
of a full PBC list, and any write-back to an auditor portal (this agent only
ever produces a draft for a human to review and send).

## Tasks
| Task | Input | Output | Autonomy |
|---|---|---|---|
| respond_to_pbc_item | pbc_item, evidence_audit_log, knowledge_base (optional) | drafted, cited PBC response submitted for approval (`null` approval request on a safety refusal) | draft-only |

## Connectors required
None. Evidence comes entirely from reusing another agent's
`platform/audit-log.AuditLogStore` (a completed `reconciliation-agent` run),
not from a live ERP/bank/document connector — this agent never touches an
external system directly (CLAUDE.md golden rule #1). Supporting citations,
when relevant, come from `platform/knowledge.KnowledgeBase`.

## Tie-out rules
1. A PBC item is structured (`period_start`, `period_end`, `evidence_type`,
   optional `source_system`) — matching runs on these fields, never on the
   free-text `description`, so tie-out stays deterministic code, not an LLM
   guess (CLAUDE.md golden rule #4).
2. `evidence_type` is validated against a small closed set
   (`audit_readiness_agent.tie_out.SUPPORTED_EVIDENCE_TYPES`); only
   `"bank_reconciliation"` is supported this iteration.
3. The evidence audit log is split into reconciliation-agent "runs" at each
   `transactions_retrieved` event. A run matches a PBC item when its recorded
   window covers the item's window (and its `source_system`, if given). Ties
   break on narrowest covering window, then most recent run.
4. A match cites the specific audit event ids and the underlying report's
   `summary` and current approval status — never mirrors or copies those
   events into this agent's own audit log (see "Evidence sourcing" below).
5. No match found is a valid, expected outcome (a "gap"), not an error — the
   drafted response still goes through approval so a human sees the open item.

## Evidence sourcing: cite, don't mirror
`evidence_audit_log` (the reconciliation-agent run being mined for evidence)
and `audit_log` (this agent's own trail) are deliberately two separate
`AuditLogStore` instances. This agent never copies another agent's raw events
into its own hash chain — a chain attests to what *that store's own agent*
did, and mirroring someone else's events into it would fabricate provenance
the chain wasn't built to carry. Instead, the tie-out result records the
source database path and the specific audit event ids it relied on, so a
human (or a real evidence-pack export) can independently open that path and
re-verify the citation at its source.

## Answering rules
1. Draft strictly from the tied-out evidence and any retrieved knowledge
   chunks — cite evidence claims by audit-event id, knowledge claims by the
   chunk's `citation` label.
2. If tie-out found no evidence, say so explicitly and flag an open item
   requiring human follow-up — never invent evidence or a root cause the
   data doesn't support.
3. The drafted response is submitted to `platform/approvals`
   (`autonomy: draft_only`) and is never treated as final until a reviewer
   and approver both sign off.
4. Every step — the tie-out result, chunks retrieved (if any), the
   model/prompt-hash used to draft, the drafted text itself, and the
   approval submission — is written to `platform/audit-log`, so a run can be
   reconstructed as auditor evidence.

Tie-out matching is deterministic code; the LLM's only job is drafting the
response's language (CLAUDE.md golden rule #4).

## Model
Default model is `claude-sonnet-5` at `output_config.effort: "medium"`
(`audit_readiness_agent.llm.DEFAULT_MODEL` / `.DEFAULT_EFFORT`), the same
cost tradeoff `technical-accounting-agent` makes while this agent is new and
still being debugged. Both are overridable per call via
`respond_to_pbc_item(model=..., effort=...)`.

## Dependency: `anthropic` SDK
Second module in the project to need it, after `technical-accounting-agent`
(see its README for why the SDK is used over hand-rolled HTTP). Scoped to
this agent's own `pyproject.toml`. The client reads `ANTHROPIC_API_KEY` from
the environment; it's never hardcoded and only ever constructed lazily (in
`runner.respond_to_pbc_item` when no `client` is passed, or in
`manual_live_run.py`) — importing `audit_readiness_agent` never requires the
key or a network connection.

## Evals
Golden test cases live in `evals/`, using a hand-written synthetic evidence
audit log (`evals/fixtures.py::build_evidence_log`, shaped like a real
`reconciliation-agent` run but not imported from that package — see the
module docstring) and a fake Anthropic client (`evals/fakes.py`, copied from
`technical-accounting-agent`'s) so the automated suite never makes a real API
call or costs real tokens:

```bash
cd agents/audit-readiness-agent && ../../.venv/bin/pytest -v
```

### Manual live run
To see the agent make a real Claude API call end to end:

```bash
ANTHROPIC_API_KEY=sk-... python agents/audit-readiness-agent/manual_live_run.py
```

This is a standalone script (not collected by pytest, never run in CI) that
builds the same synthetic evidence log the eval suite uses, ties out one PBC
item, and prints the tie-out result, the drafted response with citations, the
resulting approval request, and both stores' hash-chain verification.
