import base64
import json
from dataclasses import asdict

import pytest

from audit_log import AuditLogStore
from auth import (
    AuthStore,
    MfaAlreadyEnabled,
    MfaEnrollment,
    MfaNotEnabled,
    MfaService,
    UnknownUser,
    totp,
)

from fixtures import FICTIONAL_USERS

USER, PASSWORD, ROLE = FICTIONAL_USERS[0]


@pytest.fixture
def auth_store(tmp_path):
    s = AuthStore(tmp_path / "auth.db")
    for username, password, role in FICTIONAL_USERS:
        s.create_user(username, password, role)
    yield s
    s.close()


@pytest.fixture
def audit_log(tmp_path):
    log = AuditLogStore(tmp_path / "audit.db")
    yield log
    log.close()


@pytest.fixture(autouse=True)
def _audit_chain_stays_intact(audit_log):
    yield
    assert audit_log.verify_chain().ok is True


@pytest.fixture
def mfa(auth_store, audit_log):
    return MfaService(auth_store, audit_log=audit_log)


def _actions(audit_log):
    return [e.action for e in audit_log.get_all()]


def _serialized(audit_log):
    return json.dumps([asdict(e) for e in audit_log.get_all()], default=str)


# ---------------------------------------------------------------------------

def test_enable_returns_a_usable_secret(mfa, audit_log):
    enrollment = mfa.enable(USER)

    assert isinstance(enrollment, MfaEnrollment)
    assert enrollment.username == USER
    base64.b32decode(enrollment.secret + "=" * (-len(enrollment.secret) % 8))  # valid base32
    assert enrollment.provisioning_uri.startswith("otpauth://totp/")
    assert mfa.is_enabled(USER) is True
    assert _actions(audit_log) == ["auth.mfa.enabled"]


def test_a_valid_code_succeeds(mfa):
    secret = mfa.enable(USER).secret
    assert mfa.verify(USER, totp.generate_code(secret)) is True


def test_an_invalid_code_fails(mfa, audit_log):
    mfa.enable(USER)
    assert mfa.verify(USER, "000000") is False

    failed = audit_log.get_all()[-1]
    assert failed.action == "auth.mfa.verify_failed"
    assert failed.inputs["reason"] == "invalid"


def test_a_reused_code_fails(mfa, audit_log):
    secret = mfa.enable(USER).secret
    code = totp.generate_code(secret)

    assert mfa.verify(USER, code) is True
    assert mfa.verify(USER, code) is False  # same code, second time

    failed = audit_log.get_all()[-1]
    assert failed.action == "auth.mfa.verify_failed"
    assert failed.inputs["reason"] == "reused"


def test_a_code_from_outside_the_window_fails(mfa):
    import time

    secret = mfa.enable(USER).secret
    stale = totp.generate_code(secret, timestamp=time.time() - 120)
    assert mfa.verify(USER, stale) is False


def test_disable_removes_mfa(mfa, audit_log):
    mfa.enable(USER)
    mfa.disable(USER)

    assert mfa.is_enabled(USER) is False
    assert "auth.mfa.disabled" in _actions(audit_log)
    with pytest.raises(MfaNotEnabled):
        mfa.verify(USER, "123456")


def test_enable_twice_raises(mfa):
    mfa.enable(USER)
    with pytest.raises(MfaAlreadyEnabled):
        mfa.enable(USER)


def test_disable_when_not_enabled_raises(mfa):
    with pytest.raises(MfaNotEnabled):
        mfa.disable(USER)


def test_enable_for_an_unknown_user_raises(mfa):
    with pytest.raises(UnknownUser):
        mfa.enable("nobody.here")


def test_secret_is_never_written_to_the_audit_log(mfa, audit_log):
    enrollment = mfa.enable(USER)
    mfa.verify(USER, totp.generate_code(enrollment.secret))
    mfa.verify(USER, "000000")
    mfa.disable(USER)

    assert enrollment.secret not in _serialized(audit_log)


def test_disabling_then_re_enabling_resets_replay_protection(mfa):
    first = mfa.enable(USER).secret
    code = totp.generate_code(first)
    assert mfa.verify(USER, code) is True

    mfa.disable(USER)
    second = mfa.enable(USER).secret       # a fresh secret
    assert mfa.verify(USER, totp.generate_code(second)) is True
