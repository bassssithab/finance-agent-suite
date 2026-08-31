import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_PLATFORM = _ROOT.parent.parent / "platform"

for path in (
    _ROOT,
    _PLATFORM / "knowledge",
    _PLATFORM / "approvals",
    _PLATFORM / "audit-log",
    _PLATFORM / "connectors",
):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
