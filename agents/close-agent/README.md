# close-agent

Financial close: budget-vs-actual variance analysis. Given a period's budget
and actuals, it **deterministically** computes the variance for every line
item, flags the ones that breach configurable thresholds, drafts a cited
plain-English explanation for each flagged line when `platform/knowledge` has
relevant accounting policy, and submits the full variance report through
`platform/approvals` for human review — `autonomy: draft_only`, nothing is
final without a reviewer and approver.

## Scope

Given a `period`, a budget folder, and an actuals folder (via
`platform/connectors.FileBudgetActualConnector`, the same folder-of-CSVs
pattern `reconciliation-agent` uses for bank/ledger):

1. **Fetch** budget and actuals lines through the connector (CLAUDE.md rule #1
   — the agent never reads a CSV off disk itself). All lines for the run must
   share one currency.
2. **Compute variances** (`variance.py`) — plain `Decimal` arithmetic, no LLM
   (CLAUDE.md rule #4). Budget and actual lines are joined by
   `(account, line_item)`; a side with no row contributes `0`. Per line:
   `variance = actual - budget`, and `pct_variance = variance / budget`
   (`null` when the budget is `0` — never a divide). A `direction`
   (`over_budget` / `under_budget` / `on_budget`) is recorded; **favourable vs.
   unfavourable is deliberately not computed** — that needs account-type
   semantics and is left to the reviewer and the drafted narrative.
3. **Flag** (`variance.py`) against a configurable `FlagThresholds`:
   - `pct` — absolute percentage variance ≥ this (default `10%`; `None` disables)
   - `amount` — absolute variance ≥ this (default `None`; e.g. `Decimal("25000")`)
   - `combine` — `"any"` (either rule flags) or `"all"` (both must)
   - `flag_unbudgeted` / `flag_missing_actual` — independent safety nets for
     spend against a zero budget and a budgeted line with no actual
   Each flag records a deterministic human-readable reason string, and those
   reasons go into the audit log.
4. **Explain** (`explain.py`, only when something is flagged) — one
   `KnowledgeBase.search()` per flagged line against the `accounting_policy`
   corpus. A single Claude call (forced `record_variance_explanations` tool)
   drafts a 2–4 sentence explanation per flagged line. When policy excerpts
   were retrieved, the explanation is grounded in them and cites the excerpt
   it relied on; when none were, the explanation is drafted from the figures
   alone and the model is instructed **not** to assert a specific policy or
   accounting-standard treatment. The figures are handed to the model as
   authoritative — it never does arithmetic.
5. **Submit** the `VarianceReport` (every line item, plus the flagged items
   with their drafted explanations) to `platform/approvals`
   (`action: variance_report`) for the reviewer → approver chain.

A flagged variance does **not** stop submission — a human still reviews the
whole report. A model refusal or an unparseable response does not stop
submission either: the report goes through without explanations, with
`explanations_skipped_reason` set.

### Out of scope for this iteration
- **Close-checklist orchestration and journal-entry drafting.** Named in the
  original close-agent sketch; separate future tasks in this folder.
- **Posting anything.** The agent has no write connector — it produces a draft
  for a human, who records adjustments (or doesn't) in the ERP.
- **Materiality determination.** The agent flags on arithmetic thresholds; what
  is *material* and what is an *acceptable* explanation is the reviewer's call,
  and the prompt forbids the model from concluding either.
- **Favourable/unfavourable, multi-period trending, forecasting.** Variance is
  reported as a signed number and an arithmetic direction only.
- **Multi-currency.** A run whose lines mix currencies is rejected (same
  stance as `reconciliation-agent`'s no-FX-matching).
- **Curating the real accounting policy** — that's `platform/knowledge`'s job.
  The eval corpus (`evals/fixtures.py`) is a fictional company's policy.

## Tasks
| Task | Input | Output | Autonomy |
|---|---|---|---|
| analyze_period_variances | source_system, period, budget/actuals folders, knowledge_base, thresholds, model, effort | `VarianceReport` (all line variances, flagged items, drafted explanations) submitted for approval | draft-only |

## Connectors required
- **ERP (read)** — `platform/connectors.FileBudgetActualConnector` (the local
  folder-of-CSVs stand-in for a live planning tool / ERP export). CSV schema:
  `period,account,line_item,category,amount,currency` (`category` and
  `currency` optional). The agent never touches a bank or document connector.

## Deterministic vs. generative (CLAUDE.md rule #4)
- **Code:** fetching the lines, the join, the subtraction, the percentage, the
  quantisation, every threshold comparison and flag reason, retrieval, the
  report summary totals, the approval submission, every audit write.
- **Model:** writing the prose explanation for each flagged line, and naming
  the excerpt it relied on. The model is given the computed figures as final
  and never recomputes or disputes them.

This is arithmetic on a period's own plan and actuals, not an accounting
treatment, so no ASC/IFRS reference is encoded (same as
`reconciliation_agent.matching` and `ap_agent.sanity`).

## Audit events
Every step writes to `platform/audit-log`:

| Action | Carries |
|---|---|
| `budget_actuals_retrieved` | source_system, period, folders, budget/actual counts, currency, the serialized lines |
| `variances_computed` | thresholds used, line count, flagged count, and **for each flagged line: account, line_item, budget, actual, variance, pct, direction, and the reason(s) it was flagged** |
| `explanation_context_retrieved` *(or `variance_explanations_skipped` when nothing is flagged)* | `grounded` (were any chunks found), chunk ids, citations |
| `variance_explanations_drafted` *(or `variance_explanations_refused` / `variance_explanations_failed`)* | model, prompt hash, citations, per-line explanations; failure carries the refusal category / parse error |
| `variance_report_generated` | the deterministic summary block |
| `approval_submitted:variance_report` | emitted by `platform/approvals` on submit |

## Model
Default `claude-sonnet-5` at `output_config.effort: "medium"`
(`close_agent.explain.DEFAULT_MODEL` / `.DEFAULT_EFFORT`), the same deliberate
cost tradeoff as `agents/ap-agent`, `agents/vat-treatment-agent` and
`agents/technical-accounting-agent` while this agent is new and still being
tuned. Overridable per call via `run_close_variance_analysis(model=..., effort=...)`.

## Dependency: `anthropic` SDK
Scoped to this agent's own `pyproject.toml` (CLAUDE.md rule #5, one agent per
folder). The client reads `ANTHROPIC_API_KEY` from the environment and is only
ever constructed lazily — in `runner.run_close_variance_analysis` when no
`client` is passed **and** there is at least one flagged line to explain, or in
`manual_live_run.py`. Importing `close_agent` never needs the key or a network
connection, and a period with no flagged variances makes no API call at all.

## Sample fixtures
`evals/fixtures/{budget,actuals}/` holds three committed budget/actuals CSV
pairs. All fictional — the made-up "Larenthia Trading Co" in the invented
jurisdiction "Larenthia" (same fictional entity as `ap-agent` and
`vat-treatment-agent`):

| Period | What it exercises |
|---|---|
| `2026-07` | eventful — a large % overrun (Marketing +55%), a large-$ overrun inside 10% (COGS +$40k), a −60% under-spend, unbudgeted spend (Regulatory consulting), an on-budget line |
| `2026-08` | quiet — every line within ±5%; nothing flagged, no Claude call, report still submitted |
| `2026-09` | edge cases — a revenue miss (−14%), a budgeted line with no actual, unbudgeted bank fees |

## Evals
Golden test cases live in `evals/`, using the synthetic fixtures above, the
fictional accounting-policy corpus in `evals/fixtures.py`, and a fake Anthropic
client (`evals/fakes.py`) that returns a canned `record_variance_explanations`
tool call — so the automated suite never makes a real API call or costs tokens:

```bash
cd agents/close-agent && ../../.venv/bin/pytest -v
```

### Manual live run
To see the agent make a real Claude call end to end over the `2026-07` fixture:

```bash
ANTHROPIC_API_KEY=sk-... python agents/close-agent/manual_live_run.py
```

Standalone script (not collected by pytest, never run in CI). Uses in-memory
audit-log / approval-queue stores and prints the variance table, the flagged
lines with their reasons, the drafted explanations with citations, the
resulting approval request, and the audit log's hash-chain verification.
