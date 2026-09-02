"""Password hashing for the prototype auth layer — standard library only.

We use PBKDF2-HMAC-SHA256 (`hashlib.pbkdf2_hmac`). Both PBKDF2 and
`hashlib.scrypt` ship with CPython, but scrypt depends on the OpenSSL build
Python was linked against and can be unavailable; PBKDF2-HMAC is always
present, so it is the safe "works everywhere" choice for a prototype.

The stored value is a single self-describing string, using the same
`algorithm$iterations$salt$hash` convention as Django / passlib so the
format is familiar and the work factor can be raised later without a
schema change:

    pbkdf2_sha256$600000$<salt_b64>$<hash_b64>
"""

import base64
import hashlib
import hmac
import secrets

ALGORITHM = "pbkdf2_sha256"
ITERATIONS = 600_000
SALT_BYTES = 16
_HASH_NAME = "sha256"

# A fixed hash of a random throwaway password. login() verifies against this
# when the username is unknown, so an unknown user costs the same PBKDF2 work
# as a known one and timing does not reveal which usernames exist.
DUMMY_HASH: str


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


def hash_password(password: str, *, iterations: int = ITERATIONS) -> str:
    """Hash a plaintext password into the self-describing stored form."""
    salt = secrets.token_bytes(SALT_BYTES)
    derived = hashlib.pbkdf2_hmac(_HASH_NAME, password.encode("utf-8"), salt, iterations)
    return f"{ALGORITHM}${iterations}${_b64(salt)}${_b64(derived)}"


def verify_password(password: str, encoded: str) -> bool:
    """Constant-time check of a plaintext password against a stored hash.

    Returns False (never raises) on a malformed or unknown-algorithm hash,
    so a corrupt row cannot be told apart from a wrong password.
    """
    try:
        algorithm, iter_text, salt_b64, hash_b64 = encoded.split("$")
        if algorithm != ALGORITHM:
            return False
        iterations = int(iter_text)
        salt = _unb64(salt_b64)
        expected = _unb64(hash_b64)
    except (ValueError, TypeError):
        return False

    derived = hashlib.pbkdf2_hmac(_HASH_NAME, password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(derived, expected)


DUMMY_HASH = hash_password(secrets.token_urlsafe(32))
