import sys
from pathlib import Path

_here = Path(__file__).parent
sys.path.insert(0, str(_here))
# ScopedFileStore is gated by tenancy.TenantScope and logs to audit_log;
# tenancy in turn needs auth. Make all three importable for the test run.
sys.path.insert(0, str(_here.parent / "tenancy"))
sys.path.insert(0, str(_here.parent / "auth"))
sys.path.insert(0, str(_here.parent / "audit-log"))
