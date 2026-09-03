"""Data model for the prototype Telegram linking layer."""

from dataclasses import dataclass
from typing import Optional

__all__ = ["TelegramLink"]


@dataclass(frozen=True)
class TelegramLink:
    """The current state of one chat_id's link.

    `revoked_at` set means the link is dead: resolve_chat_id() will not
    return the user until a new code is redeemed for this chat_id.
    """

    chat_id: int
    username: str
    linked_at: str                 # ISO-8601 UTC
    linked_via: str                # sha256(code)[:12] — cross-reference only
    revoked_at: Optional[str] = None
    revoked_by: Optional[str] = None

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None
