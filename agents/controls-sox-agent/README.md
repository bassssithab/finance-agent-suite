# controls-sox-agent

SOX and internal controls. The first task tests **journal-entry approvals**
against a segregation-of-duties control: it **deterministically** checks every
entry, flags the exceptions, drafts a cited plain-English deficiency narrative
for each one when `platform/knowledge` has relevant internal-controls policy,
and submits the full control-test report through `platform/approvals` for human
review — `autonomy: draft_only`, nothing is a final controls conclusion without
a reviewer and approver.

## Scope (this iteration)

**One control, one control type: segregation of duties on journal-entry
approval.** Given a folder of journal-entry CSVs (via
`platform/connectors.FileJournalEntryConnector`, the same folder-of-CSVs
stand-in the other agents use):

1. **Fetch** the journal entries through the connector (CLAUDE.md rule #1 — the
   agent never reads a CSV off disk itself). All entries in the run must share
   one currency.
2. **Test** (`sod.py`) — plain code, no LLM (CLAUDE.md rule #4). Names are
   compared after `strip()` + `casefold()`. Per entry:
   - `no_approver` — the entry has no recorded approver at all
   - `preparer_is_approver` — the preparer also appears as `approver_1` and/or
     `approver_2` (enforced at **any** amount)
   - `duplicate_approvers` — `approver_1` and `approver_2` are the same person
     (enforced at **any** amount)
   - `missing_second_approver` — the entry's absolute value is **at or above**
     `ControlPolicy.dual_approval_threshold` (default `$50,000`, inclusive) and
     only one approver is recorded
   Each exception records a deterministic human-readable reason string, and
   those reasons go into the audit log. An entry with no exceptions `passed`.
3. **Narrate** (`narrate.py`, only when there is at least one exception) — one
   `KnowledgeBase.search()` per exception against the `internal_controls_policy`
   corpus. A single Claude call (forced `record_deficiency_narratives` tool)
   drafts a 2–4 sentence narrative per exception. When policy excerpts were
   retrieved, the narrative is grounded in them and cites the excerpt it relied
   on; when none were, it is drafted from the finding alone and the model is
   instructed **not** to assert a specific policy or framework. The
   deterministic test result is handed to the model as authoritative — it never
   re-decides whether the control failed and does no arithmetic.
4. **Submit** the `ControlTestReport` (every entry tested, plus the exceptions
   with their drafted narratives) to `platform/approvals`
   (`action: control_test_report`) for the reviewer → approver chain.

A flagged exception does **not** stop submission — a human still reviews the
whole report. A model refusal or an unparseable response does not stop
submission either: the report goes through without narratives, with
`narratives_skipped_reason` set.

### Out of scope for this iteration

- **Walkthrough documentation** and **broader deficiency tracking / remediation
  workflow.** Named in the original controls-sox-agent sketch; separate future
  tasks in this folder.
- **Any other control or control type** — access reviews, ITGCs, reconciliation
  controls, management review controls, spreadsheet controls. This task is
  journal-entry SoD only.
- **Control-*design* assessment.** The agent tests whether the control
  *operated* on the entries it was given; it does not opine on whether the
  control as designed is adequate.
- **Severity / aggregation.** The agent flags arithmetic-and-string exceptions.
  Whether an exception is a *deficiency*, a *significant deficiency*, or a
  *material weakness*, and how exceptions aggregate, is the controls owner's and
  the external auditor's call — the prompt forbids the model from concluding
  any of it.
- **Posting, blocking, or editing journal entries.** The agent has no write
  connector — it produces a draft for a human.
- **Live ERP extraction.** `FileJournalEntryConnector` is the local
  folder-of-CSVs stand-in until Phase 1 builds a connector against a live GL
  API (docs/ARCHITECTURE.md).
- **Multi-currency.** A run whose entries mix currencies is rejected (same
  stance as `reconciliation-agent` and `close-agent`).
- **Curating the real internal-controls policy** — that's `platform/knowledge`'s
  job. The eval corpus (`evals/fixtures.py`) is a fictional company's policy.

## Tasks
| Task | Input | Output | Autonomy |
|---|---|---|---|
| test_journal_entry_sod | source_system, entries_folder, knowledge_base, policy, model, effort | `ControlTestReport` (every entry tested, flagged exceptions, drafted narratives) submitted for approval | draft-only |

## Connectors required
- **ERP (read)** — `platform/connectors.FileJournalEntryConnector` (the local
  folder-of-CSVs stand-in for a live GL export). CSV schema:
  `entry_id,date,account,amount,preparer,approver_1,approver_2,currency`
  (`approver_2` and `currency` optional). The agent never touches a bank,
  document, or budget connector.

## Deterministic vs. generative (CLAUDE.md rule #4)
- **Code:** fetching the entries, name normalisation, every preparer/approver
  comparison, the distinct-approver check, the threshold comparison and every
  exception reason, retrieval, the report summary counts, the approval
  submission, every audit write.
- **Model:** writing the prose deficiency narrative for each exception, and
  naming the excerpt it relied on. The model is given the deterministic test
  result as final and never re-argues or recomputes it.

Segregation of duties over journal-entry approval is an internal-controls
concept (COSO 2013 *Control Activities*, principle 10; PCAOB AS 2201 / SOX
Section 404), not an ASC/IFRS accounting treatment, so no accounting-standard
reference is encoded in `sod.py` (same stance as `reconciliation_agent.matching`,
`ap_agent.sanity` and `close_agent.variance`). The dual-approval dollar
threshold is a company-policy input (`ControlPolicy`), not a standard.

## Audit events
Every step writes to `platform/audit-log`:

| Action | Carries |
|---|---|
| `journal_entries_retrieved` | source_system, folder, entry count, currency, the serialized entries |
| `sod_control_tested` | control id, policy used, **the list of entry_ids tested**, passed count, and **for each exception: entry_id, account, amount, preparer, approvers, dual-approval-required, the violation code, and the deterministic reason** |
| `deficiency_context_retrieved` *(or `deficiency_narratives_skipped` when nothing is flagged)* | `grounded` (were any chunks found), chunk ids, citations |
| `deficiency_narratives_drafted` *(or `deficiency_narratives_refused` / `deficiency_narratives_failed`)* | model, prompt hash, citations, per-exception narratives; failure carries the refusal category / parse error |
| `control_test_report_generated` | the deterministic summary block |
| `approval_submitted:control_test_report` | emitted by `platform/approvals` on submit |

## Model
Default `claude-sonnet-5` at `output_config.effort: "medium"`
(`controls_sox_agent.narrate.DEFAULT_MODEL` / `.DEFAULT_EFFORT`), the same
deliberate cost tradeoff as `agents/close-agent`, `agents/ap-agent` and
`agents/vat-treatment-agent` while this agent is new. Overridable per call via
`run_journal_entry_control_test(model=..., effort=...)`.

## Dependency: `anthropic` SDK
Scoped to this agent's own `pyproject.toml` (CLAUDE.md rule #5, one agent per
folder). The client reads `ANTHROPIC_API_KEY` from the environment and is only
ever constructed lazily — in `runner.run_journal_entry_control_test` when no
`client` is passed **and** there is at least one exception to narrate, or in
`manual_live_run.py`. Importing `controls_sox_agent` never needs the key or a
network connection, and a batch with no exceptions makes no API call at all.

## Sample fixtures
`evals/fixtures/journal_entries/` holds three committed CSVs. All fictional —
the made-up "Larenthia Trading Co" (the same fictional entity as `close-agent`,
`ap-agent` and `vat-treatment-agent`):

| File | What it exercises |
|---|---|
| `clean_batch.csv` | five compliant entries (two above the threshold, each with two distinct approvers) → 0 exceptions, no Claude call, report still submitted |
| `sod_violations.csv` | self-approval, a missing second approver above the threshold, duplicate approvers, and preparer-as-second-approver, plus two clean entries |
| `edge_cases.csv` | an entry exactly at the `$50,000` threshold, an entry with no approver at all, an approver name that differs only by case/whitespace, and a compliant below-threshold single-approver entry |

## Evals
Golden test cases live in `evals/`, using the synthetic fixtures above, the
fictional internal-controls-policy corpus in `evals/fixtures.py`, and a fake
Anthropic client (`evals/fakes.py`) that returns a canned
`record_deficiency_narratives` tool call — so the automated suite never makes a
real API call or costs tokens:

```bash
cd agents/controls-sox-agent && ../../.venv/bin/pytest -v
```

### Manual live run
To see the agent make a real Claude call end to end over the committed
fixtures:

```bash
ANTHROPIC_API_KEY=sk-... python agents/controls-sox-agent/manual_live_run.py
```

Standalone script (not collected by pytest, never run in CI). Uses in-memory
audit-log / approval-queue stores and prints the tested entries with their
exceptions, the drafted narratives with citations, the resulting approval
request, and the audit log's hash-chain verification.
