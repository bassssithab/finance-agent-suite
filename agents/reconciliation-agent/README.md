# reconciliation-agent

Bank and ledger reconciliation: deterministic transaction matching, exception
handling, and a human-approved reconciliation report.

## Scope
Matches bank statement lines against ledger (ERP export) lines for a given
period and produces a reconciliation report of matched pairs and unmatched
exceptions. Out of scope for this iteration: intercompany reconciliation,
multi-currency FX matching, automatic resolution of exceptions, and posting
any correcting entries (that would need write access plus its own approved
task).

## Tasks
| Task | Input | Output | Autonomy |
|---|---|---|---|
| reconcile_period | source_system, bank/ledger folders, period, tolerance_days | reconciliation report submitted for approval | draft-only |

## Connectors required
- Banking (read) — via `platform/connectors.FileConnector`
- ERP (read) — via `platform/connectors.FileConnector`

## Matching rules
1. Exact match: amount + date + reference.
2. Tolerance match: amount exact, date within `tolerance_days` (default 2).
3. Anything left over becomes a bank- or ledger-side exception.

All matching is deterministic, stdlib-only code — no LLM involvement.

## Evals
Golden test cases live in `evals/`. Agent ships only when evals pass.
