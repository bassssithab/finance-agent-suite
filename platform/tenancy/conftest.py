import sys
from pathlib import Path

_here = Path(__file__).parent
sys.path.insert(0, str(_here))
# tenancy associates auth.User objects with tenants and logs scoped writes to
# an audit_log.AuditLogStore; make both importable for the test run.
sys.path.insert(0, str(_here.parent / "auth"))
sys.path.insert(0, str(_here.parent / "audit-log"))
