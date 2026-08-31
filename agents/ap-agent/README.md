# ap-agent

Accounts-payable invoice capture. Given a scanned or photographed invoice
image, it extracts the structured fields with Claude's vision capability,
**deterministically** checks that the line items sum to the grand total, drafts
a GL account for each line when `platform/knowledge` has relevant
chart-of-accounts guidance, and submits the invoice plus suggested coding
through `platform/approvals` for human review — `autonomy: draft_only`, nothing
is final without a reviewer and approver.

## Scope

Given a `document_id` and a `platform/connectors.DocumentConnector`:

1. **Fetch** the image through the connector (CLAUDE.md rule #1 — the agent
   never reads a file off disk itself). The document's `sha256` and identity
   are logged.
2. **Extract** (`extraction.py`) via one Claude call with the image as a
   base64 `image` block and a **forced `record_invoice` tool call**, so the
   result is structured JSON, not prose to parse: vendor name, invoice number,
   date, currency, line items (description, quantity, unit price, line total),
   grand total, and a self-reported `extraction_confidence`. The model is
   instructed to transcribe only what is printed and **not** to compute or
   correct anything.
3. **Sanity-check** (`sanity.py`) — plain `Decimal` arithmetic, no LLM
   (CLAUDE.md rule #4): `sum(line_total) == grand_total` exactly, and per line
   `quantity * unit_price ≈ line_total` within a one-cent rounding tolerance.
   Any mismatch sets `discrepancy_flagged` on the draft; it does **not** stop
   submission — a human still reviews it.
4. **GL coding** (`coding.py`, optional) — one `KnowledgeBase.search()` per
   line item against the `chart_of_accounts` corpus. If nothing relevant comes
   back, this step is skipped (`gl_coding_skipped`). Otherwise a second Claude
   call (forced `record_gl_coding` tool) suggests an account per line, citing
   the chunk it relied on; a line the chart doesn't cover comes back with a
   `null` account and an explanation, never a guess.
5. **Submit** the `InvoiceDraft` to `platform/approvals`
   (`action: ap_invoice_coding`) for the reviewer → approver chain.

### Out of scope for this iteration
- **3-way match.** No PO or goods-receipt connector exists yet, so the agent
  can't match an invoice against a purchase order / receipt. That's a later
  iteration once a procurement connector lands.
- **Posting anything.** The agent has no write connector and never will need
  one — it produces a draft for a human, who posts (or doesn't) in the ERP.
- **Multi-page invoices, multiple invoices per image, non-image formats.** One
  invoice, one image (PNG/JPEG/…); PDF is accepted by the connector but the
  vision prompt assumes a single-page image.
- **Currency conversion, tax/VAT treatment, duplicate detection.** VAT
  classification is `agents/vat-treatment-agent`'s job.
- **Curating the real chart of accounts** — that's `platform/knowledge`'s job.
  The eval corpus (`evals/fixtures.py`) is a fictional company's COA.

## Tasks
| Task | Input | Output | Autonomy |
|---|---|---|---|
| extract_and_code_invoice | document_id, document_connector, knowledge_base, model, effort, coding_top_k | `InvoiceDraft` (extracted invoice, sanity-check result, `discrepancy_flagged`, GL suggestions) submitted for approval; `null` approval request on an extraction refusal / parse failure | draft-only |

## Connectors required
- **Documents (read)** — `platform/connectors.DocumentConnector` (the local
  `FileDocumentConnector` stands in until a real document store is wired). The
  agent never touches an ERP or bank connector.

## Deterministic vs. generative (CLAUDE.md rule #4)
- **Code:** fetching the image, base64-encoding it, the totals arithmetic
  (`sanity.py`), parsing model output into `Decimal` (`money.py`), retrieval,
  the approval submission, every audit write.
- **Model:** transcribing the invoice fields, assigning + explaining a GL
  account. The model never does arithmetic — if the invoice's own totals are
  wrong, it transcribes them as-is and `sanity.py` catches it.

This is arithmetic reconciliation of a document's own figures, not an
accounting treatment, so no ASC/IFRS reference is encoded (same as
`reconciliation_agent.matching`).

## Audit events
Every step writes to `platform/audit-log`:

| Action | Carries |
|---|---|
| `invoice_document_received` | document id, filename, media type, `sha256`, size |
| `invoice_extracted` *(or `invoice_extraction_failed`)* | model, prompt hash, extracted header fields, **`extraction_confidence`**; failure carries refusal category / parse error |
| `sanity_check_completed` | **`discrepancy_flagged`**, computed line sum vs. stated grand total, the difference, any per-line issues |
| `gl_coding_suggested` *(or `gl_coding_skipped` / `gl_coding_failed`)* | model, prompt hash, chunk ids, suggestions + citations |
| `approval_submitted:ap_invoice_coding` | emitted by `platform/approvals` on submit |

## Model
Default `claude-sonnet-5` at `output_config.effort: "medium"`
(`ap_agent.extraction.DEFAULT_MODEL` / `.DEFAULT_EFFORT`), the same deliberate
cost tradeoff as `agents/vat-treatment-agent` and
`agents/technical-accounting-agent` while this agent is new and still being
tuned — not a claim that Sonnet is the right long-term tier for invoice OCR.
Overridable per call via `process_invoice(model=..., effort=...)`.

## Dependency: `anthropic` SDK
Scoped to this agent's own `pyproject.toml` (CLAUDE.md rule #5, one agent per
folder). The client reads `ANTHROPIC_API_KEY` from the environment and is only
ever constructed lazily (in `runner.process_invoice` when no `client` is
passed, or in `manual_live_run.py`) — importing `ap_agent` never needs the key
or a network connection.

`pillow` is a **dev-only** dependency (`[project.optional-dependencies] dev`),
used solely by `evals/generate_sample_invoices.py` to regenerate the fixture
images. Nothing at runtime or in the test suite imports it.

## Sample invoice fixtures
`evals/fixtures/invoices/` holds three committed PNGs plus a
`<slug>.expected.json` per image (the ground truth, in `record_invoice` tool
shape). All fictional — invented vendors in the made-up jurisdiction
"Larenthia", each image stamped `SAMPLE — NOT A REAL INVOICE`:

| Fixture | What it exercises |
|---|---|
| `clean_office_supplies` | happy path — 3 lines, totals tie exactly, codes to one account |
| `consulting_services` | services lines (`quantity × rate`), totals tie, reimbursable-travel coding rule |
| `mismatched_totals` | grand total is wrong (1690.00 of lines vs. 1609.00 printed) — the deterministic check must flag **+81.00** |

Regenerate after editing the ground truth:

```bash
pip install -e 'agents/ap-agent[dev]'
python agents/ap-agent/evals/generate_sample_invoices.py
```

## Evals
Golden test cases live in `evals/`, using the synthetic fixtures above, the
fictional chart of accounts in `evals/fixtures.py`, and a fake Anthropic client
(`evals/fakes.py`) that dispatches on the forced tool name — so the automated
suite never makes a real API call or costs tokens:

```bash
cd agents/ap-agent && ../../.venv/bin/pytest -v
```

### Manual live run
To see the agent make real Claude vision + coding calls end to end over all
three sample invoices:

```bash
ANTHROPIC_API_KEY=sk-... python agents/ap-agent/manual_live_run.py
```

Standalone script (not collected by pytest, never run in CI). Uses in-memory
audit-log / approval-queue stores and prints, per invoice, the extracted
fields and confidence, the sanity-check verdict, the drafted GL coding with
citations, and the resulting approval request — then the audit log's hash-chain
verification.
