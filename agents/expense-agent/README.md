# expense-agent

Travel-and-expense receipt policy compliance. Given a photo or scan of a
receipt, it extracts the structured fields with Claude's vision capability,
**deterministically** checks them against a configurable expense policy
(per-category spending limits, a maximum receipt age, required fields present),
drafts a cited explanation of which policy rule any flagged item breaches when
`platform/knowledge` has relevant policy, and submits the extracted expense plus
the compliance result through `platform/approvals` for human review —
`autonomy: draft_only`, nothing is final without a reviewer and approver.

## Scope

Given a `document_id` and a `platform/connectors.DocumentConnector` (the **same**
`FileDocumentConnector` `ap-agent` uses — no new connector), plus an
`ExpensePolicy` and an `as_of_date` (defaults to today):

1. **Fetch** the receipt image through the connector (CLAUDE.md rule #1 — the
   agent never reads a file off disk itself). The document's `sha256` and
   identity are logged.
2. **Extract** (`extraction.py`) via one Claude call with the image as a base64
   `image` block and a **forced `record_receipt` tool call**: `vendor`, `date`
   (as printed), `amount`, `currency`, `expense_category` (the model's best-guess
   label from the merchant + line items — the one field it infers rather than
   transcribes), and a self-reported `extraction_confidence`. The model is told
   to transcribe only what is printed and **not** to compute or judge anything.
3. **Compliance check** (`compliance.py`) — plain `date`/`Decimal` code, no LLM
   (CLAUDE.md rule #4). Against a configurable `ExpensePolicy`:
   - **`category_limits`** (`{category: Decimal}`) — `amount` strictly greater
     than the limit for the receipt's category is a `category_over_limit`
     violation. Category is matched **case/whitespace-insensitively, exact match
     only** — a label the map doesn't contain falls back to `default_limit`, or
     is uncapped when that is `None`. The agent does **not** guess that "Team
     dinner" means "meals" (a deliberate limitation — the reviewer and the
     drafted explanation handle ambiguous labels).
   - **`max_receipt_age_days`** — a receipt older than this many days before
     `as_of_date` is a `receipt_too_old` violation (inclusive: exactly N days
     old passes). `None` disables the check.
   - **`required_fields`** (default vendor/date/amount/currency/expense_category)
     — any that came back empty is a `missing_required_field` violation.
   - The receipt `date` is parsed ISO-first, then via `date_formats` (default
     ISO only); an unparseable date is a `date_unparseable` violation and the
     age check is skipped.
   Every violation carries a deterministic human-readable `detail`, and those go
   into the audit log. A violation sets `compliance_flagged`; it does **not**
   stop submission — a human still reviews it.
4. **Explain** (`explain.py`, optional) — **only when there is ≥1 violation and**
   one `KnowledgeBase.search()` per violation against the `expense_policy`
   corpus returns chunks. A single Claude call (forced
   `record_policy_explanations` tool) drafts a 2–4 sentence note per violation:
   which written rule applies and why this receipt breaches it, grounded
   strictly in the retrieved excerpts and citing each. No violations → skipped
   (`no_violations`); violations but nothing relevant retrieved → skipped
   (`no_relevant_knowledge`); a refusal or unparseable response → skipped
   (`explanation_refused` / `explanation_failed`). In every skip case the draft
   is still submitted. The deterministic violation is handed to the model as
   authoritative — it never re-decides or recomputes it.
5. **Submit** the `ExpenseDraft` to `platform/approvals`
   (`action: expense_policy_check`) for the reviewer → approver chain.

On an extraction refusal or parse failure, **nothing is submitted** — the
failure is logged and the run returns `draft=None, approval_request=None`.

### Out of scope for this iteration
- **Duplicate and fraud detection.** Named in the original expense-agent sketch;
  a separate future task in this folder (needs a history/corpus of prior
  claims).
- **Reimbursement posting or claim approval.** The agent has no write connector —
  it produces a draft; a human approves and the payment runs elsewhere. The
  prompt forbids the model from deciding the outcome or conceding a waiver.
- **Itemised line-item OCR, per-diem math, mileage, split allocations.** One
  receipt, one total.
- **Multi-receipt images, non-image formats.** One receipt, one image
  (PNG/JPEG/…); PDF is accepted by the connector but the vision prompt assumes a
  single-page image.
- **Multi-currency / FX.** Limits are bare numbers compared to the receipt
  amount; the receipt currency is recorded and shown but not converted.
- **Fuzzy category mapping.** Exact-match only (see above).
- **Curating the real expense policy** — that's `platform/knowledge`'s job. The
  eval corpus (`evals/fixtures.py`) is a fictional company's policy.

## Tasks
| Task | Input | Output | Autonomy |
|---|---|---|---|
| check_receipt_policy_compliance | document_id, document_connector, knowledge_base, policy, as_of_date, model, effort, retrieval_top_k | `ExpenseDraft` (extracted receipt, compliance result, `compliance_flagged`, cited explanations) submitted for approval; `null` approval request on an extraction refusal / parse failure | draft-only |

## Connectors required
- **Documents (read)** — `platform/connectors.DocumentConnector` /
  `FileDocumentConnector`, **reused unchanged from `ap-agent`** (its docstring
  already names "a receipt" as a use case). The agent never touches an ERP or
  bank connector.

## Deterministic vs. generative (CLAUDE.md rule #4)
- **Code:** fetching the image, base64-encoding it, parsing the model's `amount`
  into `Decimal` (`money.py`), parsing the receipt date, every limit / age /
  required-field comparison and its reason string (`compliance.py`), retrieval,
  the approval submission, every audit write.
- **Model:** transcribing the receipt fields, inferring a category label, and
  writing the cited policy explanation. The model never does arithmetic and
  never decides whether the expense is in policy.

This is arithmetic and date math against a company T&E policy, not an accounting
treatment, so no ASC/IFRS reference is encoded (same stance as
`reconciliation_agent.matching`, `ap_agent.sanity` and `close_agent.variance`).

## Audit events
Every step writes to `platform/audit-log`:

| Action | Carries |
|---|---|
| `receipt_document_received` | document id, filename, media type, `sha256`, size |
| `receipt_extracted` *(or `receipt_extraction_failed`)* | model, prompt hash, vendor/date/amount/currency/**expense_category**/**extraction_confidence**; failure carries refusal category / parse error |
| `compliance_check_completed` | **`compliance_flagged`**, the policy applied, parsed date, applied limit, `as_of_date`, and **for each violation: code, field, deterministic detail** |
| `policy_explanation_drafted` *(or `policy_explanation_skipped` / `policy_explanation_failed`)* | model, prompt hash, chunk ids, per-violation explanations + citations; skip carries the reason |
| `approval_submitted:expense_policy_check` | emitted by `platform/approvals` on submit |

## Model
Default `claude-sonnet-5` at `output_config.effort: "medium"`
(`expense_agent.extraction.DEFAULT_MODEL` / `.DEFAULT_EFFORT`), the same
deliberate cost tradeoff as `agents/ap-agent` and `agents/vat-treatment-agent`
while this agent is new — not a claim that Sonnet is the right long-term tier for
receipt OCR. Overridable per call via
`check_receipt_policy_compliance(model=..., effort=...)`.

## Dependencies
- **`anthropic`** — scoped to this agent's own `pyproject.toml` (CLAUDE.md rule
  #5). Client reads `ANTHROPIC_API_KEY`, built lazily only in
  `runner.check_receipt_policy_compliance` (when no `client` is passed) or
  `manual_live_run.py`. Importing `expense_agent` never needs the key.
- **`pillow`** — **dev-only** (`[project.optional-dependencies] dev`), used
  solely by `evals/generate_sample_receipts.py`. Nothing at runtime or in the
  test suite imports it.
- **`money.py`** is a near-copy of `ap_agent/money.py`, kept local so this
  build touched only one agent folder. FOLLOW-UP: consolidate both into a small
  shared `platform/` helper.

## Sample receipt fixtures
`evals/fixtures/receipts/` holds three committed PNGs plus a
`<slug>.expected.json` per image (the ground truth, in `record_receipt` tool
shape). All fictional — invented merchants in the made-up jurisdiction
"Larenthia", each image stamped `SAMPLE — NOT A REAL RECEIPT`, all dated relative
to `AS_OF = 2026-09-01`:

| Fixture | What it exercises |
|---|---|
| `compliant_taxi` | `$38.40` taxi fare, category "Travel - taxi", 4 days old, all fields present → **passes**, explanation skipped |
| `over_limit_dinner` | `$182.50` restaurant meal, category "Meals", recent → `category_over_limit` (+ cited explanation) |
| `stale_hotel` | `$212.00` hotel bill within the lodging cap but ~5 months old → `receipt_too_old` |

`missing_required_field` and `date_unparseable` are covered in `test_compliance.py`
with hand-built receipts — no image needed.

Regenerate after editing the ground truth:

```bash
pip install -e 'agents/expense-agent[dev]'
python agents/expense-agent/evals/generate_sample_receipts.py
```

## Evals
Golden test cases live in `evals/`, using the synthetic fixtures above, the
fictional expense policy in `evals/fixtures.py`, and a fake Anthropic client
(`evals/fakes.py`) that dispatches on the forced tool name — so the automated
suite never makes a real API call or costs tokens:

```bash
cd agents/expense-agent && ../../.venv/bin/pytest -v
```

### Manual live run
To see the agent make real Claude vision + explanation calls end to end over all
three sample receipts:

```bash
ANTHROPIC_API_KEY=sk-... python agents/expense-agent/manual_live_run.py
```

Standalone script (not collected by pytest, never run in CI). Uses in-memory
audit-log / approval-queue stores and prints, per receipt, the extracted fields
and confidence, the compliance verdict with any violations, the drafted
explanations with citations, and the resulting approval request — then the audit
log's hash-chain verification.
