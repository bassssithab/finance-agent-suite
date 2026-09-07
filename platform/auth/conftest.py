import sys
from pathlib import Path

_here = Path(__file__).parent
sys.path.insert(0, str(_here))
# auth composes approvals (Role) and audit-log (MfaService / PasswordResetService
# write to an injected AuditLogStore); make both importable for the test run
# regardless of import order within a test file.
sys.path.insert(0, str(_here.parent / "approvals"))
sys.path.insert(0, str(_here.parent / "audit-log"))
