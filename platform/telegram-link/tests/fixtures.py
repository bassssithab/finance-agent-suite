"""Fictional users and chat_ids for the Telegram linking prototype.

Every user, password, and chat_id here is invented. Not real people, not
real Telegram chats. Do not reuse.
"""

from auth import Role

# username -> (password, role)
FICTIONAL_USERS = {
    "dana.acme": ("acme-pw-placeholder", Role.APPROVER),
    "farah.globex": ("globex-pw-placeholder", Role.REVIEWER),
    "evan.acme": ("acme-pw-placeholder-2", Role.PREPARER),
}

# Fictional Telegram chat ids (real ones are ints; groups can be negative).
CHAT_DANA = 700001
CHAT_FARAH = 700002
CHAT_SPARE = 700003

# Fictional admin identity for revocations.
ADMIN = "admin.root"
