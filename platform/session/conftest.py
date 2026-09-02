import sys
from pathlib import Path

_here = Path(__file__).parent
sys.path.insert(0, str(_here))
# This module composes auth + tenancy + audit-log; make all three importable
# for the test run. (auth/__init__.py also adds ../approvals.)
sys.path.insert(0, str(_here.parent / "auth"))
sys.path.insert(0, str(_here.parent / "tenancy"))
sys.path.insert(0, str(_here.parent / "audit-log"))
