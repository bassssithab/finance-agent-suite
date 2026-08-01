# platform/approvals

Shared chassis component. See docs/ARCHITECTURE.md.

Human-in-the-loop approval queue: agents submit draft actions, humans move
them through **preparer → reviewer → approver** before anything with an
external effect (posting a JE, sending an email, filing something) is
allowed to execute. Segregation of duties is enforced — the same actor
cannot act twice on one request (e.g. the preparer can't also approve their
own draft).

`audit_log.AuditLogStore` remains the tamper-evident system of record; every
submit/approve/edit/reject here writes one event to it. This module's own
SQLite tables just hold the queue's mutable operational state (what's
pending, in whose queue) plus a decision history for convenient lookups.

## Usage

```python
from audit_log import AuditLogStore
from approvals import ApprovalQueue, Decision, Role

audit_log = AuditLogStore("audit.db")
queue = ApprovalQueue("approvals.db", audit_log)

request = queue.submit(
    agent="reconciliation-agent",
    action="draft_journal_entry",
    payload={"debit": "1000", "credit": "2000", "amount": 500},
    preparer="reconciliation-agent",
    timestamp="2026-08-01T12:00:00Z",
)

queue.decide(request.id, actor="bob", role=Role.REVIEWER,
             decision=Decision.APPROVE, timestamp="2026-08-01T12:05:00Z")

final = queue.decide(request.id, actor="carol", role=Role.APPROVER,
                      decision=Decision.APPROVE, timestamp="2026-08-01T12:10:00Z")

assert final.status == "approved"   # now safe to execute the external effect
```

## Development

```bash
# from repo root, one-time setup
python3 -m venv .venv
.venv/bin/pip install pytest

# run tests
cd platform/approvals && ../../.venv/bin/pytest -v
```

No install step is needed for the `approvals` or `audit_log` packages —
`conftest.py` puts `approvals/` on `sys.path` for the test run, and
`approvals/__init__.py` adds `../audit-log` to `sys.path` so `import
audit_log` resolves without a separate install.
