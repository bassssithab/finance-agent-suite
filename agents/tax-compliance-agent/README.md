# tax-compliance-agent

Tax compliance: a **period-end VAT provision calculation with a filing-support
narrative**. Given a period's transactions, it **deterministically** computes
output VAT (VAT on sales) and input VAT (VAT on purchases) by treatment
category, nets them into the period's payable-or-refundable position, flags the
anomalies worth scrutiny, drafts a plain-English filing-support narrative, and
submits the whole provision through `platform/approvals` for human review —
`autonomy: draft_only`. **The narrative never claims the return is correct or
ready to file** — that is the reviewer's and ultimately a qualified tax
professional's call.

## Scope

Given a folder of VAT-transaction CSVs (via
`platform/connectors.FileVatTransactionConnector`) and a `ProvisionPolicy`:

1. **Fetch** the transactions through the connector (CLAUDE.md rule #1 — the
   agent never reads a CSV off disk itself). All rows must share one currency.
2. **Compute** (`provision.py`) — plain `Decimal` arithmetic, no LLM (CLAUDE.md
   rule #4). Each transaction's `transaction_type` and `vat_treatment` are
   canonicalised (`strip().casefold()`; `"zero-rated export"` → `"zero-rated"`).
   Per transaction: **output VAT** = `amount × vat_rate` for a standard-rated
   **sale** (a missing rate contributes `0` and is flagged); **input VAT** =
   `amount × vat_rate` for a standard-rated **purchase**; `0` for zero-rated /
   exempt / out-of-scope. Amounts quantised to cents. A **by-treatment
   breakdown** (each of the four categories × {sale, purchase} → count, net
   amount, VAT) is produced.
3. **Net** — `net_vat = output_vat_total − input_vat_total`; `position` =
   `payable` (`>0`) / `refundable` (`<0`) / `nil` (`0`).
4. **Flag anomalies** (`provision.py`) with deterministic reason strings:
   | code | rule |
   |---|---|
   | `net_refundable_position` | `net_vat < 0` (whole period; `policy.flag_refundable`, default on) — unusual for a mostly standard-rated trader |
   | `unrecognized_treatment` | `vat_treatment` blank or not one of the four categories — the transaction is **excluded from the VAT totals** and flagged |
   | `treatment_rate_mismatch` | standard-rated with no rate or a zero rate; **or** zero-rated / exempt / out-of-scope carrying a nonzero rate |
   | `unrecognized_transaction_type` | `transaction_type` not `sale` / `purchase` — excluded from the totals and flagged (safety net so an unclassifiable row can't silently distort the net) |
   | *(off by default)* `unexpected_standard_rate` | `policy.expected_standard_rate` is set and a standard-rated rate differs from it |
   A transaction excluded from the totals means the net position can be
   **understated** — the narrative calls this out and the reviewer resolves it.
5. **Narrate** (`narrate.py`) — **always** one Claude call (there is always a
   VAT position to summarise). One `KnowledgeBase.search()` per anomaly + one
   for the position, against the `vat_policy` corpus. A forced
   `record_filing_support_narrative` tool call returns a `position_summary`, an
   `anomaly_explanations` list (one per anomaly, each saying **whether it needs
   a tax specialist**), a `specialist_review_needed` boolean, and `citations`.
   Grounded in the retrieved excerpts when any; ungrounded (no invented rules)
   otherwise.
6. **Submit** the `VatProvisionReport` to `platform/approvals`
   (`action: vat_provision`) for the reviewer → approver chain.

A flagged anomaly does **not** stop submission. A model refusal or an
unparseable narrative does not stop submission either: the deterministic
calculation is complete on its own, so the report goes through without a
narrative, with `narrative_skipped_reason` set.

### The narrative's guard-rails (`narrate.py` `SYSTEM_PROMPT`)

- The output VAT, input VAT, net position and flagged anomalies are **final and
  authoritative** — the model never recomputes, adjusts, or disputes them, and
  does no arithmetic of its own.
- The model **must not state or imply that the filing is correct, complete,
  accurate, reconciled, or ready to submit** — no "this can be filed", "the
  return is ready", "no issues", "everything ties out". Whether the return can
  be filed is the reviewer's decision and ultimately a qualified tax
  professional's.
- Every anomaly is **flagged for specialist review, not resolved** — an
  unrecognised treatment, a treatment/rate mismatch, or a net refundable
  position are not for the model to explain away.
- This is a draft for human review, **not a filed return and not tax advice**.

### Consistency with `vat-treatment-agent`

This agent uses the **same fictional Larenthia VAT code** that
`agents/vat-treatment-agent` classifies against: the same four treatment
categories (`standard-rated`, `zero-rated`, `exempt`, `out-of-scope`), the same
**15% standard rate**, and the same input-VAT recovery rules. Those shared rules
are **restated** in this agent's own `evals/fixtures.py` (same `corpus="vat_policy"`
name) rather than imported, so the folder stays self-contained (CLAUDE.md rule
#5, and the repo pattern where every agent's `evals/fixtures.py` is standalone);
the filing-and-provision documents (period return, specialist-review triggers)
are new to this agent.

### Out of scope for this iteration
- **Partial exemption / input-VAT attribution, reverse charge, VAT groups,
  bad-debt relief, multi-rate schemes.** One standard rate, four categories,
  output-less-recoverable-input.
- **Filing the return.** The agent has no write connector — it produces a draft
  provision; a qualified person files.
- **Deciding whether the return is correct or can be filed.** The agent flags;
  the reviewer and a tax professional decide (the prompt forbids the model from
  concluding it).
- **Cross-period adjustments, prior-period corrections, VAT account
  reconciliation.**
- **Multi-currency.** A run whose transactions mix currencies is rejected.
- **Curating the real VAT corpus** — that's `platform/knowledge`'s job.

## Tasks
| Task | Input | Output | Autonomy |
|---|---|---|---|
| calculate_vat_provision | source_system, transactions_folder, knowledge_base, policy, period_label, model, effort | `VatProvisionReport` (computed transactions, by-treatment breakdown, net position, anomalies, narrative) submitted for approval | draft-only |

## Connectors required
- **ERP (read)** — `platform/connectors.FileVatTransactionConnector` (the local
  folder-of-CSVs stand-in for a live ERP tax report). CSV schema:
  `transaction_id,date,transaction_type,amount,vat_treatment,vat_rate,currency`
  (`vat_treatment`, `vat_rate` and `currency` optional; `vat_rate` blank → `None`,
  distinct from `0`). The agent never touches a bank or document connector.

## Deterministic vs. generative (CLAUDE.md rule #4)
- **Code:** fetching the transactions, canonicalising the treatment/type
  strings, every output/input VAT multiplication and the netting, the
  by-treatment breakdown, every anomaly check and its reason string, retrieval,
  the report summary, the approval submission, every audit write.
- **Model:** writing the position summary and the per-anomaly explanations, and
  deciding — from the anomalies it is handed — whether a specialist is needed.
  The model is given the computed figures as final.

This module encodes the arithmetic of a period return, not an accounting
treatment, so no ASC/IFRS reference is encoded (same as
`reconciliation_agent.matching` and `close_agent.variance`).

## Audit events
Every step writes to `platform/audit-log`:

| Action | Carries |
|---|---|
| `vat_transactions_retrieved` | source_system, folder, period_label, txn count, currency, date range, the serialized transactions |
| `vat_provision_computed` | policy used, output/input VAT totals, net_vat, position, by-treatment breakdown, transactions excluded from totals, and **for each anomaly: code, transaction_id, deterministic detail** |
| `filing_guidance_context_retrieved` | `grounded` (were any chunks found), chunk ids, citations |
| `filing_support_narrative_drafted` *(or `filing_support_narrative_refused` / `filing_support_narrative_failed`)* | model, prompt hash, citations, the drafted narrative (position_summary + anomaly_explanations + specialist_review_needed); failure carries the refusal category / parse error |
| `vat_provision_report_generated` | the deterministic summary block |
| `approval_submitted:vat_provision` | emitted by `platform/approvals` on submit |

## Model
Default `claude-sonnet-5` at `output_config.effort: "medium"`
(`tax_compliance_agent.narrate.DEFAULT_MODEL` / `.DEFAULT_EFFORT`), the same
deliberate cost tradeoff as `agents/close-agent`, `agents/fpa-agent` and
`agents/vat-treatment-agent` while this agent is new. Overridable per call via
`run_vat_provision(model=..., effort=...)`. **This agent always makes one Claude
call** — there is always a narrative to draft.

## Dependency: `anthropic` SDK
Scoped to this agent's own `pyproject.toml` (CLAUDE.md rule #5). The client reads
`ANTHROPIC_API_KEY` from the environment and is only ever constructed lazily —
in `runner.run_vat_provision` when no `client` is passed, or in
`manual_live_run.py`. Importing `tax_compliance_agent` never needs the key.

## Sample fixtures
`evals/fixtures/transactions/` holds three committed CSVs — all fictional,
"Larenthia Trading Co", standard rate `0.15`:

| File | What it exercises |
|---|---|
| `normal_payable.csv` | standard-rated domestic sales, a zero-rated export, an out-of-scope drop-ship, standard-rated purchases → net **payable $14,550.00**, no anomalies |
| `refundable_period.csv` | a slow sales month against large standard-rated capex purchases → net **refundable −$14,700.00** → `net_refundable_position` |
| `data_quality_issues.csv` | a standard-rated sale with **no rate**, a zero-rated sale carrying **rate 0.15**, a `"reduced-rated"` treatment, a `"refund"` transaction_type → `treatment_rate_mismatch` ×2, `unrecognized_treatment`, `unrecognized_transaction_type`; two transactions excluded from the totals |

## Evals
Golden test cases live in `evals/`, using the synthetic fixtures above, the
fictional Larenthia VAT corpus in `evals/fixtures.py`, and a fake Anthropic
client (`evals/fakes.py`) that returns a canned `record_filing_support_narrative`
tool call — so the automated suite never makes a real API call or costs tokens:

```bash
cd agents/tax-compliance-agent && ../../.venv/bin/pytest -v
```

### Manual live run
To see the agent make a real Claude call end to end over all three sample
periods:

```bash
ANTHROPIC_API_KEY=sk-... python agents/tax-compliance-agent/manual_live_run.py
```

Standalone script (not collected by pytest, never run in CI). Uses in-memory
audit-log / approval-queue stores and prints, per period, the output/input VAT
and net position, the anomalies with their reasons, the drafted narrative
(position summary + per-anomaly explanations + `specialist_review_needed`) with
citations, the resulting approval request, and the audit log's hash-chain
verification.
