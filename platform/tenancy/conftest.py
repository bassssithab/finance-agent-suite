import sys
from pathlib import Path

_here = Path(__file__).parent
sys.path.insert(0, str(_here))
# tenancy associates auth.User objects with tenants; make `auth` importable
# for the test run (auth/__init__.py in turn adds ../approvals and ../audit-log).
sys.path.insert(0, str(_here.parent / "auth"))
