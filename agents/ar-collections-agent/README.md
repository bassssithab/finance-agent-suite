# ar-collections-agent

Accounts-receivable collections. Given a book of open invoices, it
**deterministically** ages every invoice (days overdue + aging bucket), flags
the invoices that warrant a collection action against configurable rules, drafts
a dunning email for each flagged invoice whose **tone escalates with how overdue
the invoice is**, grounds each email in `platform/knowledge` collections policy
when relevant, and submits the full aging report plus the drafted emails through
`platform/approvals` for human review — `autonomy: draft_only`, and **the agent
never sends an email**.

## Scope

Given a folder of open-invoice CSVs (via
`platform/connectors.FileOpenInvoiceConnector`, the same folder-of-CSVs pattern
the other agents use) and an `as_of_date` (defaults to today):

1. **Fetch** the open invoices through the connector (CLAUDE.md rule #1 — the
   agent never reads a CSV off disk itself). All invoices in the run must share
   one currency.
2. **Age** (`aging.py`) — plain `date`/`int`/`Decimal` code, no LLM (CLAUDE.md
   rule #4). Per invoice: `days_overdue = as_of_date − due_date` (may be ≤ 0),
   and an aging **bucket**:

   | Bucket | `days_overdue` |
   |---|---|
   | `current` | ≤ 0 |
   | `1-30` | 1–30 |
   | `31-60` | 31–60 |
   | `61-90` | 61–90 (inclusive) |
   | `90+` | ≥ 91 (strictly more than 90 days past due) |

3. **Flag** (`aging.py`) against a configurable `DunningPolicy`:
   - `min_days_overdue` (default `31`) — flag any single invoice at/past this many days overdue
   - `flag_repeat_customers` / `repeat_customer_min_overdue_invoices` (default `True` / `2`) —
     additionally flag **every** overdue (`days_overdue ≥ 1`) invoice of a
     customer who has at least N overdue invoices in the batch, so a customer
     slipping across several invoices is chased even on the ones inside
     `min_days_overdue`
   - `min_amount` (default `None`) — suppress flagging for balances below this
   Each flag records a deterministic human-readable reason string, and those
   reasons go into the audit log.
4. **Tone tier** (`aging.py`, deterministic) — assigned from `days_overdue`, not
   chosen by the model:

   | Tier | `days_overdue` | Voice |
   |---|---|---|
   | `reminder` | ≤ 60 | gentle, assume oversight — also repeat-customer 1–30 invoices |
   | `firm` | 61–90 | direct, payment now required |
   | `formal` | ≥ 91 | final-notice seriousness |

5. **Draft** (`draft.py`, only when something is flagged) — one
   `KnowledgeBase.search()` per flagged invoice against the `collections_policy`
   corpus. A single Claude call (forced `record_dunning_drafts` tool) drafts one
   email (subject + body) per flagged invoice **in the tone tier it was
   assigned**. When policy excerpts were retrieved, the email is grounded in
   them and cites the excerpt it relied on; when none were, the email is a plain
   factual payment request and the model is instructed **not** to invent a late
   fee, interest, a legal step, a collections-agency referral, credit-bureau
   reporting, or a service suspension. The amount, due date and days-overdue
   figures are handed to the model as authoritative — it never does arithmetic
   and never re-picks the tone (the tone stored on the draft is always the
   deterministic one).
6. **Submit** the `CollectionsReport` (every aged invoice, plus the flagged
   invoices with their drafted emails) to `platform/approvals`
   (`action: collections_report`) for the reviewer → approver chain.

A flagged invoice does **not** stop submission — a human still reviews the whole
report. A model refusal or an unparseable response does not stop submission
either: the report goes through without drafts, with `drafts_skipped_reason`
set.

### Out of scope for this iteration
- **Sending email.** The agent has no write/email connector — it produces a
  draft for a human, who sends (or doesn't) from the AR system.
- **Cash application, payment-plan negotiation, dispute handling, credit
  holds/limits, write-offs, dunning-cadence scheduling.** Named in the original
  ar-collections sketch; separate future tasks in this folder. The prompt
  forbids the model from conceding a discount, waiver, or payment plan.
- **Statement-of-account / multi-invoice consolidated dunning.** One email per
  flagged invoice this iteration.
- **Interest and late fees.** Not computed. Only mentioned in an email if a
  cited policy excerpt explicitly supports it.
- **Multi-currency.** A run whose invoices mix currencies is rejected (same
  stance as `reconciliation-agent` / `close-agent` / `controls-sox-agent`).
- **Curating the real collections policy** — that's `platform/knowledge`'s job.
  The eval corpus (`evals/fixtures.py`) is a fictional company's policy.

## Tasks
| Task | Input | Output | Autonomy |
|---|---|---|---|
| draft_dunning_for_aging | source_system, invoices_folder, knowledge_base, as_of_date, policy, model, effort | `CollectionsReport` (every aged invoice, flagged items, drafted dunning emails) submitted for approval | draft-only |

## Connectors required
- **ERP (read)** — `platform/connectors.FileOpenInvoiceConnector` (the local
  folder-of-CSVs stand-in for a live AR sub-ledger export). CSV schema:
  `invoice_id,customer,invoice_date,due_date,amount,currency,last_payment_date`
  (`currency` and `last_payment_date` optional). The agent never touches a bank,
  document, budget, or journal-entry connector.

## Deterministic vs. generative (CLAUDE.md rule #4)
- **Code:** fetching the invoices, days-overdue arithmetic, bucket assignment,
  every flagging-rule comparison and flag reason, the tone-tier assignment,
  retrieval, the report summary totals and bucket/tone breakdowns, the approval
  submission, every audit write.
- **Model:** writing the subject and body of each dunning email in the assigned
  tone, and naming the excerpt it relied on. The model is given the computed
  figures and the assigned tone as final.

This is arithmetic on invoice dates and a company dunning policy, not an
accounting treatment, so no ASC/IFRS reference is encoded (same as
`reconciliation_agent.matching`, `ap_agent.sanity` and `close_agent.variance`).

## Audit events
Every step writes to `platform/audit-log`:

| Action | Carries |
|---|---|
| `open_invoices_retrieved` | source_system, folder, `as_of_date`, invoice count, currency, the serialized invoices |
| `aging_computed` | policy used, every invoice's `{invoice_id, customer, days_overdue, bucket}`, and **for each flagged invoice: amount, days_overdue, bucket, tone_tier, and the reason(s) it was flagged** |
| `dunning_context_retrieved` *(or `dunning_drafts_skipped` when nothing is flagged)* | `grounded` (were any chunks found), chunk ids, citations |
| `dunning_drafts_drafted` *(or `dunning_drafts_refused` / `dunning_drafts_failed`)* | model, prompt hash, citations, per-invoice drafts; failure carries the refusal category / parse error |
| `collections_report_generated` | the deterministic summary block |
| `approval_submitted:collections_report` | emitted by `platform/approvals` on submit |

## Model
Default `claude-sonnet-5` at `output_config.effort: "medium"`
(`ar_collections_agent.draft.DEFAULT_MODEL` / `.DEFAULT_EFFORT`), the same
deliberate cost tradeoff as `agents/close-agent`, `agents/controls-sox-agent`
and `agents/ap-agent` while this agent is new. Overridable per call via
`run_ar_collections_analysis(model=..., effort=...)`.

## Dependency: `anthropic` SDK
Scoped to this agent's own `pyproject.toml` (CLAUDE.md rule #5, one agent per
folder). The client reads `ANTHROPIC_API_KEY` from the environment and is only
ever constructed lazily — in `runner.run_ar_collections_analysis` when no
`client` is passed **and** there is at least one flagged invoice, or in
`manual_live_run.py`. Importing `ar_collections_agent` never needs the key or a
network connection, and a book with nothing flagged makes no API call at all.

## Sample fixtures
`evals/fixtures/open_invoices/` holds three committed CSVs, all aged against
`AS_OF = 2026-09-01`. All fictional — the made-up "Larenthia Trading Co" (the
same fictional entity as `close-agent`, `controls-sox-agent` and `ap-agent`):

| File | What it exercises |
|---|---|
| `current_book.csv` | five invoices, all `current` or `1-30`, none meeting any rule → 0 flagged, no Claude call, report still submitted |
| `mixed_aging.csv` | a `31-60` invoice (`reminder`), a `61-90` invoice (`firm`), and customer "Halvar Logistics" with two overdue invoices — one at 20 days pulled in by the repeat-customer rule (`reminder`) — plus two current invoices |
| `severe_delinquency.csv` | three `90+` invoices from one customer (`formal`, and that customer has three overdue invoices), one `61-90` invoice (`firm`), and one current invoice |

## Evals
Golden test cases live in `evals/`, using the synthetic fixtures above, the
fictional collections-policy corpus in `evals/fixtures.py`, and a fake Anthropic
client (`evals/fakes.py`) that returns a canned `record_dunning_drafts` tool
call — so the automated suite never makes a real API call or costs tokens:

```bash
cd agents/ar-collections-agent && ../../.venv/bin/pytest -v
```

### Manual live run
To see the agent make a real Claude call end to end over the `mixed_aging`
fixture:

```bash
ANTHROPIC_API_KEY=sk-... python agents/ar-collections-agent/manual_live_run.py
```

Standalone script (not collected by pytest, never run in CI). Uses in-memory
audit-log / approval-queue stores and prints the aging table, the flagged
invoices with their reasons and tone tiers, the drafted emails with citations,
the resulting approval request, and the audit log's hash-chain verification.
