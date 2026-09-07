"""TOTP unit tests, including the RFC 6238 test vectors.

The RFC vectors are the interoperability proof: if this module produces the
exact codes the RFC publishes for a known secret at known times, then any
authenticator app that follows the RFC (Google Authenticator, Authy,
1Password, ...) will accept these secrets and produce codes this module
verifies. Self-consistency (generate_code matching verify) would not prove
that.
"""

import base64
import time

import pytest

from auth import totp

# RFC 6238 Appendix B — SHA-1, seed = ASCII "12345678901234567890".
_RFC_SECRET = base64.b32encode(b"12345678901234567890").decode("ascii")
_RFC_VECTORS = [
    (59, "94287082"),
    (1111111109, "07081804"),
    (1111111111, "14050471"),
    (1234567890, "89005924"),
    (2000000000, "69279037"),
    (20000000000, "65353130"),
]


@pytest.mark.parametrize("unix_time, expected", _RFC_VECTORS)
def test_matches_rfc6238_published_vectors(unix_time, expected):
    assert totp.generate_code(_RFC_SECRET, timestamp=unix_time, digits=8) == expected


# ---------------------------------------------------------------------------

def test_current_code_verifies():
    secret = totp.generate_secret()
    assert totp.verify(secret, totp.generate_code(secret))


def test_adjacent_time_windows_are_accepted():
    secret = totp.generate_secret()
    t = 1_700_000_000
    assert totp.verify(secret, totp.generate_code(secret, timestamp=t - 30), timestamp=t)
    assert totp.verify(secret, totp.generate_code(secret, timestamp=t + 30), timestamp=t)


def test_code_from_far_outside_the_window_is_rejected():
    secret = totp.generate_secret()
    t = 1_700_000_000
    stale = totp.generate_code(secret, timestamp=t - 120)  # 4 steps back, window is +/-1
    assert totp.verify(secret, stale, timestamp=t) is False


def test_wrong_code_is_rejected():
    secret = totp.generate_secret()
    assert totp.verify(secret, "000000") is False
    assert totp.verify(secret, "abcdef") is False


def test_generate_secret_is_valid_base32():
    secret = totp.generate_secret()
    assert "=" not in secret
    raw = base64.b32decode(secret + "=" * (-len(secret) % 8))
    assert len(raw) == 20


def test_matching_step_returns_the_step():
    secret = totp.generate_secret()
    t = 1_700_000_000
    assert totp.matching_step(secret, totp.generate_code(secret, timestamp=t), timestamp=t) == totp.step_for(t)
    assert totp.matching_step(secret, "000000", timestamp=t) is None


def test_provisioning_uri_shape():
    secret = totp.generate_secret()
    uri = totp.provisioning_uri(secret, account_name="dana.acme", issuer="finance-agent-suite")
    assert uri.startswith("otpauth://totp/finance-agent-suite:dana.acme?")
    assert f"secret={secret}" in uri
    assert "issuer=finance-agent-suite" in uri
    assert "algorithm=SHA1" in uri
    assert "digits=6" in uri
    assert "period=30" in uri


def test_verify_defaults_to_now():
    secret = totp.generate_secret()
    # generated against the real clock, no timestamp passed anywhere
    assert totp.verify(secret, totp.generate_code(secret))
    assert abs(totp.step_for(time.time()) - totp.matching_step(secret, totp.generate_code(secret))) <= 1
