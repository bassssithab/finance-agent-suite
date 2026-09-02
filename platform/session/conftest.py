import sys
from pathlib import Path

_here = Path(__file__).parent
sys.path.insert(0, str(_here))
# This module composes auth + tenancy; make both importable for the test run.
# (auth/__init__.py adds ../approvals; tenancy/__init__.py adds ../auth too.)
sys.path.insert(0, str(_here.parent / "auth"))
sys.path.insert(0, str(_here.parent / "tenancy"))
