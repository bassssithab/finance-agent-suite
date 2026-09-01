# regulatory-change-agent

Regulatory change: a **first-pass impact triage**. Given a plain-text
description of one new or changed regulatory requirement and the company's
internal-controls register, it **deterministically** scores each control for
keyword/category overlap with the requirement, flags a likely gap when nothing
appears to address it, drafts a plain-English impact-assessment narrative, and
submits the triage through `platform/approvals` for human review —
`autonomy: draft_only`.

## This is a triage tool, not a compliance determination

**Read this first.** The relevance match is a crude keyword/category overlap
computed by simple code — **it is not legal reasoning.** A keyword match does
not mean a control satisfies the requirement, and the absence of a match does
not prove a real gap. The agent **never concludes whether the company is or
isn't compliant**, and `narrate.py`'s `SYSTEM_PROMPT` forbids the model from
saying "no action needed", "the company meets this", "this is handled", "there
is a gap", or "the requirement is satisfied". The narrative must state — in the
assessment **and** in a dedicated `review_required_statement` field — that a
**qualified legal and/or compliance professional must review the requirement
against the controls regardless of what the triage found.** The whole output is
an input to that review, never a substitute for it.

## Scope

Given a `requirement_text` (a free-text string — **not a document**), a folder
of internal-controls CSVs (via
`platform/connectors.FileInternalControlConnector`), and a `TriagePolicy`:

1. **Fetch** the controls register through the connector (CLAUDE.md rule #1 —
   the agent never reads a CSV off disk itself).
2. **Triage** (`triage.py`) — plain code, no LLM (CLAUDE.md rule #4).
   `key_terms()` turns free text into a deterministic set of terms: lower-case,
   split on non-alphanumerics, drop tokens < 3 chars, drop a builtin stopword
   set (+ `policy.extra_stopwords`), then a **lightweight stemmer** (plurals /
   `-ing` / `-ed` only — documented as "not a real stemmer"). Per control:
   `score = |requirement terms ∩ (description terms ∪ category terms)|`;
   `category_match` = a requirement term appears in the control's category;
   `matched_terms` = the sorted overlap. A control is `relevant` when
   `score >= policy.min_keyword_overlap` (default `2`). The surfaced shortlist
   is sorted `(score desc, control_id asc)` and capped at
   `policy.max_controls_surfaced`.
3. **Verdict** (`triage.py`):
   | verdict | rule | `gap_flagged` |
   |---|---|---|
   | `likely_gap` | no control reaches `min_keyword_overlap` | **True** |
   | `weak_coverage` | a control is relevant but the strongest score `< policy.strong_overlap` (default `4`) | **True** |
   | `apparent_coverage` | the strongest relevant control scores `>= strong_overlap` | False |
   Each flagged verdict records a deterministic reason string.
4. **Assess** (`narrate.py`) — **always** one Claude call (there is always a
   triage to summarise). One `KnowledgeBase.search()` per requirement + one per
   surfaced control against the `regulatory_guidance` corpus. A forced
   `record_impact_assessment` tool call returns: an `assessment`, a
   `relevant_controls_explained` list (`{control_id, explanation}` per surfaced
   control), a `gap_explanation` (present only when `gap_flagged`), a
   `review_required_statement`, and `citations`. Grounded in the retrieved
   excerpts when any; ungrounded (no invented regulatory rules) otherwise.
5. **Submit** the `TriageReport` to `platform/approvals`
   (`action: change_triage`) for the reviewer → approver chain.

A flagged gap does **not** stop submission. A model refusal or an unparseable
narrative does not stop submission either: the deterministic triage is complete
on its own, so the report goes through without a narrative, with
`narrative_skipped_reason` set.

### Out of scope for this iteration
- **Any legal or regulatory interpretation.** Keyword overlap only.
- **Any compliance conclusion.** The agent flags for review; it never decides.
- **Multi-requirement batches, jurisdiction detection, effective-date tracking,
  control-design adequacy, remediation planning, obligation registers.**
- **Reading a regulation document.** The requirement is a free-text field a
  human writes; parsing PDFs of regulations is a later task.
- **Curating the real regulatory-guidance corpus** — that's
  `platform/knowledge`'s job. The eval corpus (`evals/fixtures.py`) is a
  fictional company's procedure.

## Consistency with `controls-sox-agent`

This agent's controls-list input shares the `control_id,description,category`
shape that `controls-sox-agent` uses, but they read different things through
different connectors: `controls-sox-agent` reads **journal entries**
(`FileJournalEntryConnector`) to test a control's *operation*; this agent reads
the **controls register itself** (`FileInternalControlConnector`) to triage it
against a requirement. The fictional entity ("Larenthia Trading Co") is shared.

## Tasks
| Task | Input | Output | Autonomy |
|---|---|---|---|
| triage_regulatory_change | source_system, requirement_text, controls_folder, knowledge_base, requirement_reference, policy, model, effort | `TriageReport` (scored controls, surfaced shortlist, coverage verdict, gap flag, narrative) submitted for approval | draft-only |

## Connectors required
- **ERP (read)** — `platform/connectors.FileInternalControlConnector` (the local
  folder-of-CSVs stand-in for a live GRC-tool export). CSV schema:
  `control_id,description,category` (`category` optional). The agent never
  touches a bank, document, or transaction connector.

## Deterministic vs. generative (CLAUDE.md rule #4)
- **Code:** fetching the controls, tokenising and stemming the requirement and
  each control, the overlap scoring, the ranking, the coverage verdict and every
  flag reason, retrieval, the report summary, the approval submission, every
  audit write.
- **Model:** writing the impact-assessment prose and per-control explanations.
  The model is given the keyword-triage output as the triage's result and never
  re-scores or re-matches, and it never concludes on compliance.

This module does string matching, not interpretation, so no ASC/IFRS or legal
reference is encoded.

## Audit events
Every step writes to `platform/audit-log`:

| Action | Carries |
|---|---|
| `regulatory_change_received` | the full `requirement_text`, `requirement_reference` |
| `controls_retrieved` | source_system, folder, control count, the serialized controls |
| `impact_triaged` | policy used, control count, relevant-control count, `coverage_verdict`, `gap_flagged`, `flag_reasons`, and **per surfaced control: control_id, category, score, matched_terms, category_match** |
| `triage_context_retrieved` | `grounded` (were any chunks found), chunk ids, citations |
| `impact_assessment_drafted` *(or `impact_assessment_refused` / `impact_assessment_failed`)* | model, prompt hash, citations, the drafted narrative (assessment + per-control explanations + gap_explanation + review_required_statement); failure carries the refusal category / parse error |
| `triage_report_generated` | the deterministic summary block |
| `approval_submitted:change_triage` | emitted by `platform/approvals` on submit |

## Model
Default `claude-sonnet-5` at `output_config.effort: "medium"`
(`regulatory_change_agent.narrate.DEFAULT_MODEL` / `.DEFAULT_EFFORT`), the same
deliberate cost tradeoff as `agents/close-agent`, `agents/fpa-agent` and
`agents/tax-compliance-agent` while this agent is new. Overridable per call via
`run_change_triage(model=..., effort=...)`. **This agent always makes one Claude
call.**

## Dependency: `anthropic` SDK
Scoped to this agent's own `pyproject.toml` (CLAUDE.md rule #5). The client reads
`ANTHROPIC_API_KEY` from the environment and is only ever constructed lazily —
in `runner.run_change_triage` when no `client` is passed, or in
`manual_live_run.py`. Importing `regulatory_change_agent` never needs the key.

## Sample scenarios
`evals/fixtures/controls/company_controls.csv` holds ten fictional controls
("Larenthia Trading Co") across Access Management, Data Protection, Financial
Reporting, Vendor Management, Change Management, Incident Response and Training.
`evals/fixtures.py`'s `SCENARIOS` defines three requirement strings against it:

| Scenario | Requirement (free text) | Verdict |
|---|---|---|
| `clear_match` | privileged access must require MFA and quarterly recertification | **`apparent_coverage`** — CTL-101 overlaps on 9 terms |
| `genuine_gap` | notify the supervisory authority of a reportable security breach within 72 hours | **`likely_gap`** — no control shares ≥ 2 terms (only the noise word "security") |
| `ambiguous` | documented data retention schedules with periodic disposal of stale records | **`weak_coverage`** — CTL-104/CTL-103 overlap on "data / personal / records" but not the retention concept |

## Evals
Golden test cases live in `evals/`, using the synthetic fixtures above, the
fictional regulatory-guidance corpus in `evals/fixtures.py`, and a fake
Anthropic client (`evals/fakes.py`) that returns a canned
`record_impact_assessment` tool call — so the automated suite never makes a real
API call or costs tokens:

```bash
cd agents/regulatory-change-agent && ../../.venv/bin/pytest -v
```

### Manual live run
To see the agent make a real Claude call end to end over all three scenarios:

```bash
ANTHROPIC_API_KEY=sk-... python agents/regulatory-change-agent/manual_live_run.py
```

Standalone script (not collected by pytest, never run in CI). Uses in-memory
audit-log / approval-queue stores and prints, per scenario, the coverage verdict
with its reasons, the surfaced controls with matched terms, the drafted
assessment + per-control explanations + `gap_explanation` +
`review_required_statement` with citations, the resulting approval request, and
the audit log's hash-chain verification.
