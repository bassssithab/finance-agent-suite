# platform/audit-log

Shared chassis component. See docs/ARCHITECTURE.md.

Append-only, hash-chained event store. Every agent action (input retrieved,
model/version used, prompt hash, output, human approver, timestamp) is
recorded here and can never be edited or deleted — only appended.

Backed by local SQLite for now (`AuditLogStore(path)`). Two independent
safeguards protect the log: SQLite triggers reject any `UPDATE`/`DELETE`
against the `events` table, and `verify_chain()` recomputes the sha256 hash
chain to detect tampering even if the triggers were bypassed.

## Usage

```python
from audit_log import AuditEvent, AuditLogStore

store = AuditLogStore("audit.db")

store.append(AuditEvent(
    timestamp="2026-08-01T12:00:00Z",
    agent="reconciliation-agent",
    action="draft_journal_entry",
    actor="system",
    inputs={"bank_statement_id": "stmt-1"},
    output={"match_count": 3},
    model="claude-sonnet-5",
    prompt_hash="...",
    approval_status="draft",
))

result = store.verify_chain()   # ChainVerificationResult(ok=True, ...)
store.export_evidence_pack("evidence_pack.json")
```

## Development

```bash
# from repo root, one-time setup
python3 -m venv .venv
.venv/bin/pip install pytest

# run tests
cd platform/audit-log && ../../.venv/bin/pytest -v
```

No install step is needed for the `audit_log` package itself — `conftest.py`
in this directory puts it on `sys.path` for the test run.
