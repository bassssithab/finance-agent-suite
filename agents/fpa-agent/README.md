# fpa-agent

Financial planning & analysis: a **simple driver-based forecast with a
narrative**. Given a set of historical actuals and a set of forward growth
assumptions, it **deterministically** projects every line item forward N
periods, flags the lines whose assumed growth rate implies an unusually large
change, drafts a plain-English forecast narrative, and submits the whole
forecast through `platform/approvals` for human review — `autonomy: draft_only`,
nothing is a committed plan without a reviewer and approver.

## Scope

Given a folder of historical-actuals CSVs (via
`platform/connectors.FileBudgetActualConnector`, **reused unchanged** from
`close-agent` — this agent passes only an `actuals_folder`), a
`ForecastAssumptions`, and a `horizon`:

1. **Fetch** the actuals through the connector (CLAUDE.md rule #1 — the agent
   never reads a CSV off disk itself). All lines must share one currency.
   Periods are monthly `YYYY-MM` strings only this iteration; anything else is
   rejected.
2. **Project** (`projection.py`) — plain `Decimal` / calendar arithmetic, no LLM
   (CLAUDE.md rule #4). The **base period** is the most recent period present.
   Per `(account, line_item)`: the base value is its amount in the base period;
   a line absent there is **carried forward** from its last actual and flagged
   `stale_base` (a real line is never silently dropped). The assumed rate is
   `assumptions.category_growth.get(category, assumptions.default_growth)`,
   compounded: `projected[k] = base * (1 + rate)**k`, quantised to cents. One
   `ProjectedLine` per line per future period.
3. **Flag** (`projection.py`) with deterministic reason strings:
   - `high_sensitivity` — `|assumed rate| >= assumptions.max_pop_change_pct`
     (default 25%)
   - `projection_turns_negative` — the rate drives a projected period below zero
     (`assumptions.flag_negative`, default on)
   - `stale_base` — the base was carried forward from before the base period
4. **Narrate** (`narrate.py`) — **always** one Claude call (unlike `close-agent`,
   there is always a trajectory and assumptions to summarise). One
   `KnowledgeBase.search()` per forecast + one per flagged line against the
   `fpa_methodology` corpus. A forced `record_forecast_narrative` tool call
   returns: a `summary` of the trajectory, `assumptions_described` (each
   assumption restated **as an assumption**), `flagged_items_called_out` (one
   per flagged line), and `citations`. When policy excerpts were retrieved the
   narrative is grounded in them and cites them; when none were, it is drafted
   from the figures alone and the model is told **not** to assert a specific
   methodology. The figures are handed to the model as authoritative — it never
   does arithmetic.
5. **Submit** the `ForecastReport` (all projected lines, the flagged items, the
   narrative) to `platform/approvals` (`action: forecast`) for the reviewer →
   approver chain.

A flagged line does **not** stop submission. A model refusal or an unparseable
narrative does not stop submission either: the deterministic projection is
complete on its own, so the forecast goes through without a narrative, with
`narrative_skipped_reason` set.

### Anti-overconfidence design (the point of this agent's prompt)

A forecast is a forward-looking projection built on assumptions, and
overconfidence here is a real risk. `narrate.py`'s `SYSTEM_PROMPT` **explicitly
forbids** the model from:
- presenting any projected figure as a certainty, a guarantee, a commitment, or
  a statement of what "will" happen (it must stay projective — "projected",
  "under these assumptions", "if the assumed rates hold");
- attaching confidence levels or probabilities it was not given;
- describing a growth rate as a measured or verified trend — every rate is **an
  assumption the planner supplied** and must be described as one;
- recommending a decision, endorsing the plan, or concluding the forecast is
  reasonable / achievable / conservative / aggressive — that is the reviewer's
  judgement.
It is also required to call out **every** flagged high-sensitivity line by name
and say a small change in its assumption moves the forecast materially.

### Out of scope for this iteration
- **Seasonality, regression, curve-fitting, ML forecasting.** Growth is a flat
  compounded rate per category, nothing more.
- **Scenario comparison, rolling reforecast, forecast-vs-actual tracking,
  variance-to-plan.** One forecast, one set of assumptions.
- **Non-monthly periods** (`YYYY-Qn`, `YYYY`), **multi-currency.** Rejected.
- **Judging whether an assumption is reasonable.** The agent flags on the
  arithmetic size of the assumed rate; whether that rate is *supportable* is the
  reviewer's call, and the prompt forbids the model from concluding it.
- **Curating the real FP&A methodology** — that's `platform/knowledge`'s job.
  The eval corpus (`evals/fixtures.py`) is a fictional company's methodology.

## Tasks
| Task | Input | Output | Autonomy |
|---|---|---|---|
| project_driver_based_forecast | source_system, actuals_folder, knowledge_base, assumptions, horizon, model, effort | `ForecastReport` (all projected lines, flagged items, narrative) submitted for approval | draft-only |

## Connectors required
- **ERP (read)** — `platform/connectors.FileBudgetActualConnector` with only an
  `actuals_folder` (every line comes back tagged `source_capability="actuals"`).
  CSV schema: `period,account,line_item,category,amount,currency` (`category`
  and `currency` optional). No new connector — the same one `close-agent` uses.

## Deterministic vs. generative (CLAUDE.md rule #4)
- **Code:** fetching the actuals, finding the base period, advancing the
  calendar, applying and compounding the assumed rate, every threshold
  comparison and flag reason, the report summary totals, retrieval, the approval
  submission, every audit write.
- **Model:** writing the narrative prose, restating each assumption as an
  assumption, and calling out the flagged lines. The model is given the computed
  figures as final and never recomputes or disputes them.

This is arithmetic on a company's own actuals and its own forward assumptions,
not an accounting treatment, so no ASC/IFRS reference is encoded (same as
`reconciliation_agent.matching` and `close_agent.variance`).

## Audit events
Every step writes to `platform/audit-log`:

| Action | Carries |
|---|---|
| `historical_actuals_retrieved` | source_system, folder, horizon, line count, currency, distinct historical periods, the serialized lines |
| `forecast_projected` | assumptions used, base_period, projected_periods, line count, flagged count, and **for each flagged line: account, line_item, category, growth_rate, growth_source, projected_amount, flag_reasons** |
| `narrative_context_retrieved` | `grounded` (were any chunks found), chunk ids, citations |
| `forecast_narrative_drafted` *(or `forecast_narrative_refused` / `forecast_narrative_failed`)* | model, prompt hash, citations, the drafted narrative (summary + assumptions_described + flagged_items_called_out); failure carries the refusal category / parse error |
| `forecast_report_generated` | the deterministic summary block |
| `approval_submitted:forecast` | emitted by `platform/approvals` on submit |

## Model
Default `claude-sonnet-5` at `output_config.effort: "medium"`
(`fpa_agent.narrate.DEFAULT_MODEL` / `.DEFAULT_EFFORT`), the same deliberate cost
tradeoff as `agents/close-agent` and `agents/ar-collections-agent` while this
agent is new. Overridable per call via
`run_driver_based_forecast(model=..., effort=...)`. **This agent always makes one
Claude call** — there is always a narrative to draft.

## Dependency: `anthropic` SDK
Scoped to this agent's own `pyproject.toml` (CLAUDE.md rule #5, one agent per
folder). The client reads `ANTHROPIC_API_KEY` from the environment and is only
ever constructed lazily — in `runner.run_driver_based_forecast` when no `client`
is passed, or in `manual_live_run.py`. Importing `fpa_agent` never needs the key
or a network connection.

## Sample fixtures
`evals/fixtures/actuals/` holds three committed monthly CSVs — all fictional,
"Larenthia Trading Co" (the same fictional entity as `close-agent` and
`ap-agent`):

| File | What it contributes |
|---|---|
| `2026-04.csv` | six lines across Revenue, Cost of sales, Operating expenses |
| `2026-05.csv` | same six lines, mild upward drift |
| `2026-06.csv` | five lines — **"Software subscriptions" drops out**, so the base period lacks it and the carry-forward / `stale_base` path is exercised |

The eval suite drives different `ForecastAssumptions` on top of this one history:
default 2% (nothing flagged but `stale_base`), aggressive 30% Revenue growth
(revenue lines `high_sensitivity`), and negative growth (`projection_turns_negative`).

## Evals
Golden test cases live in `evals/`, using the synthetic fixtures above, the
fictional FP&A-methodology corpus in `evals/fixtures.py`, and a fake Anthropic
client (`evals/fakes.py`) that returns a canned `record_forecast_narrative` tool
call — so the automated suite never makes a real API call or costs tokens:

```bash
cd agents/fpa-agent && ../../.venv/bin/pytest -v
```

### Manual live run
To see the agent make a real Claude call end to end over the sample actuals with
an aggressive Revenue growth assumption:

```bash
ANTHROPIC_API_KEY=sk-... python agents/fpa-agent/manual_live_run.py
```

Standalone script (not collected by pytest, never run in CI). Uses in-memory
audit-log / approval-queue stores and prints the projection table, the flagged
lines with their reasons, the drafted narrative (summary + assumptions + flagged
call-outs) with citations, the resulting approval request, and the audit log's
hash-chain verification.
