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
