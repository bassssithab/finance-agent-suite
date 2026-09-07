import sys
from pathlib import Path

_here = Path(__file__).parent
sys.path.insert(0, str(_here))
# Analytics is tenant-scoped via tenancy.require_scope / TenantScope; tenancy
# in turn needs auth, which needs audit-log. Make all importable for the test run.
sys.path.insert(0, str(_here.parent / "tenancy"))
sys.path.insert(0, str(_here.parent / "auth"))
sys.path.insert(0, str(_here.parent / "audit-log"))
