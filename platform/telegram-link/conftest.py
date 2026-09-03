import sys
from pathlib import Path

_here = Path(__file__).parent
sys.path.insert(0, str(_here))
# This module composes auth + audit-log; make both importable for the test run.
sys.path.insert(0, str(_here.parent / "auth"))
sys.path.insert(0, str(_here.parent / "audit-log"))
