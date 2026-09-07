"""RFC 6238 time-based one-time passwords — standard library only.

Hand-rolled, like auth.passwords hand-rolls PBKDF2, rather than pulling in
pyotp. TOTP is a small, fully-specified algorithm (RFC 6238 = HOTP/RFC 4226
with a time-derived counter), and interoperability is verified against the
RFC's own published test vectors (see tests/test_totp.py) — so any
authenticator app that follows the RFC (Google Authenticator, Authy, 1Password,
...) will accept these secrets and produce codes this module verifies.

SHA-1 / 6 digits / 30-second period — the defaults every authenticator app
assumes when a provisioning URI omits them.
"""

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote, urlencode

PERIOD = 30
DIGITS = 6
ALGORITHM = "SHA1"
DEFAULT_WINDOW = 1  # accept the current 30s step +/- 1 (~+/-30s of clock skew)

_SECRET_BYTES = 20  # 160 bits, the RFC 4226 recommendation


def _b32_pad(secret: str) -> str:
    return secret.upper() + "=" * (-len(secret) % 8)


def generate_secret() -> str:
    """A fresh base32 secret (unpadded) suitable for a QR code or manual entry."""
    return base64.b32encode(secrets.token_bytes(_SECRET_BYTES)).decode("ascii").rstrip("=")


def step_for(timestamp: float) -> int:
    """The RFC 6238 time-step counter for a Unix timestamp."""
    return int(timestamp // PERIOD)


def code_for_step(secret: str, step: int, *, digits: int = DIGITS) -> str:
    """The HOTP value for a given counter — the core of RFC 4226."""
    key = base64.b32decode(_b32_pad(secret))
    mac = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    binary = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(binary % (10 ** digits)).zfill(digits)


def generate_code(secret: str, *, timestamp: float = None, digits: int = DIGITS) -> str:
    """The current 6-digit code (or at `timestamp`, for tests/clients)."""
    ts = time.time() if timestamp is None else timestamp
    return code_for_step(secret, step_for(ts), digits=digits)


def matching_step(
    secret: str,
    code: str,
    *,
    timestamp: float = None,
    valid_window: int = DEFAULT_WINDOW,
) -> int:
    """The time-step `code` matches within [-valid_window, +valid_window], or
    None. Constant-time comparison per candidate step. Callers that need
    replay protection compare the returned step against the last one they
    accepted (see AuthStore.consume_totp)."""
    ts = time.time() if timestamp is None else timestamp
    current = step_for(ts)
    submitted = str(code).strip()
    for offset in range(-valid_window, valid_window + 1):
        step = current + offset
        if hmac.compare_digest(code_for_step(secret, step), submitted):
            return step
    return None


def verify(
    secret: str,
    code: str,
    *,
    timestamp: float = None,
    valid_window: int = DEFAULT_WINDOW,
) -> bool:
    """True if `code` is valid now (or at `timestamp`). No replay protection —
    that lives in AuthStore.consume_totp, which is the only place a code is
    actually 'spent'."""
    return matching_step(secret, code, timestamp=timestamp, valid_window=valid_window) is not None


def provisioning_uri(secret: str, *, account_name: str, issuer: str) -> str:
    """An otpauth:// URI for a QR code. Contains the secret — treat it exactly
    as sensitively as the secret itself; never log it."""
    # issuer and account are encoded individually; the ":" separator between
    # them stays literal, as authenticator apps expect (the Key URI Format).
    label = f"{quote(issuer, safe='')}:{quote(account_name, safe='')}"
    params = urlencode({
        "secret": secret,
        "issuer": issuer,
        "algorithm": ALGORITHM,
        "digits": DIGITS,
        "period": PERIOD,
    })
    return f"otpauth://totp/{label}?{params}"
