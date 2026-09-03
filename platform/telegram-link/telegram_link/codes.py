"""One-time linking codes — pure functions, standard library only.

A linking code is a short-lived credential a user pastes to a Telegram bot.
It must be unguessable (not sequential, not time-derived) and it is stored
only as its SHA-256 hash — a leak of the code_codes table cannot be used to
redeem outstanding codes (the same principle auth applies to session
tokens).
"""

import hashlib
import hmac
import re
import secrets

# secrets.token_urlsafe(n) returns ~4/3 * n characters from [A-Za-z0-9_-].
# 9 bytes -> exactly 12 characters (72 bits, no padding) -> ~72 bits of entropy.
_CODE_BYTES = 9
_CODE_LEN = 12
_CODE_SHAPE = re.compile(rf"[A-Za-z0-9_-]{{{_CODE_LEN}}}")


def new_code() -> str:
    """A fresh, unguessable 12-character code."""
    return secrets.token_urlsafe(_CODE_BYTES)


def looks_like_code(text: str) -> bool:
    """True if `text` is shaped like one of our codes: 12 characters from the
    urlsafe-base64 alphabet, once surrounding whitespace is trimmed.

    Deliberately permissive — this is a routing hint, not validation. A false
    positive just becomes a loud, friendly failure from redeem_link_code().
    """
    return _CODE_SHAPE.fullmatch(normalize(text)) is not None


def normalize(code: str) -> str:
    """Canonical form for lookup. token_urlsafe is case-sensitive (base64url),
    so we only trim surrounding whitespace — never change case."""
    return code.strip()


def code_hash(code: str) -> str:
    return hashlib.sha256(normalize(code).encode("utf-8")).hexdigest()


def fingerprint(code: str) -> str:
    """A short, non-reversible tag safe to write to the audit log."""
    return code_hash(code)[:12]


def matches(code: str, stored_hash: str) -> bool:
    return hmac.compare_digest(code_hash(code), stored_hash)
