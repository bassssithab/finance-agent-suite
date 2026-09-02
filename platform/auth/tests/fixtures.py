"""Fictional accounts for exercising the prototype auth layer.

Every entry here is invented. These are not real people and the passwords
are throwaway placeholders that exist only so the tests have something to
hash. Do not reuse them anywhere.
"""

from auth import Role

FICTIONAL_USERS = [
    ("ada.ledger", "correct-horse-battery-staple", Role.APPROVER),
    ("bruno.tally", "hunter2-but-a-lot-longer", Role.REVIEWER),
    ("cleo.vouchers", "prototype-pw-please-do-not-reuse", Role.PREPARER),
]
