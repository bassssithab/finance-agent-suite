# platform/connectors

Shared chassis component. See docs/ARCHITECTURE.md.

Read-first connector interface: agents get read access to ERP/bank/document
data through a `Connector`, never by talking to the external system
directly (CLAUDE.md golden rule #1). There are no write methods here —
posting/filing actions always go through `platform/approvals` instead.

## `FileConnector`

The only concrete connector so far: a local-folder stand-in for a real bank
feed / ERP export, used for local dev and testing before Phase 1 builds a
connector against a live API.

Expected CSV schemas:
- **Bank statement**: `date,account,description,amount,balance,reference` —
  `amount` is already signed (deposit positive, withdrawal negative).
- **Ledger export**: `date,account,memo,debit,credit,reference` — normalized
  to `amount = debit - credit` (positive = net debit).

Both normalize to the same `Transaction` record (`source_capability` is
`"bank"` or `"erp"`).

## Usage

```python
from datetime import date
from connectors import FileConnector

connector = FileConnector(
    source_system="sample_co",
    bank_folder="./sample_data/bank",
    ledger_folder="./sample_data/ledger",
)

transactions = connector.fetch_transactions(
    start_date=date(2026, 7, 1), end_date=date(2026, 7, 31),
)
```

Reading data isn't itself an approval-gated action, so `FileConnector`
doesn't write to `platform/audit-log` — the calling agent records what it
retrieved as part of its own `AuditEvent` when it later takes an action.

## `FileDocumentConnector`

Read-only stand-in for a real document store / email-attachment source
(SharePoint, Drive, S3, email — see docs/ARCHITECTURE.md), for agents that
work from binary source documents rather than normalized rows. It reads files
from a local folder and returns a `SourceDocument` (raw `content` bytes,
`media_type`, `size_bytes`, and a `sha256` over the content).

```python
from connectors import FileDocumentConnector

docs = FileDocumentConnector(source_system="sample_co", folder="./sample_data/invoices")
docs.list_documents()                     # ["invoice_0417.png", ...]
doc = docs.fetch_document("invoice_0417.png")
doc.media_type                            # "image/png"
```

Supported types: PNG, JPEG, GIF, WebP, PDF. `document_id` is the bare filename
within `folder`; ids containing path separators or `..` are rejected. Like
`FileConnector`, it never writes to `platform/audit-log` — the calling agent
records the `sha256` and identity of the document it acted on in its own
`AuditEvent`.

## `FileBudgetActualConnector`

Read-only stand-in for a real budget / actuals feed (a planning tool or ERP
export), for agents that work from a period's plan-vs-actual line items rather
than transactions or documents. It reads CSVs from a budget folder and an
actuals folder and returns `BudgetActualLine` records (`source_capability` is
`"budget"` or `"actuals"`, the same way `FileConnector` tags `"bank"` vs
`"erp"`).

Expected CSV schema (budget and actuals are identical in shape):

```
period,account,line_item,category,amount,currency
```

`category` and `currency` are optional — blank/missing `category` becomes `""`,
blank/missing `currency` becomes `"USD"`. `period`, `account`, and `line_item`
are required; a blank one raises `ConnectorParseError`.

```python
from connectors import FileBudgetActualConnector

connector = FileBudgetActualConnector(
    source_system="sample_co",
    budget_folder="./sample_data/budget",
    actuals_folder="./sample_data/actuals",
)

lines = connector.fetch_lines(period="2026-07")   # exact-match period filter
```

Like the other connectors it never writes to `platform/audit-log` — the calling
agent records what it retrieved in its own `AuditEvent`.

## Development

```bash
# from repo root, one-time setup
python3 -m venv .venv
.venv/bin/pip install pytest

# run tests
cd platform/connectors && ../../.venv/bin/pytest -v
```

No install step is needed — `conftest.py` puts `connectors/` on `sys.path`
for the test run.
