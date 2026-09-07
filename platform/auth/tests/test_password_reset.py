import json
import sqlite3
import statistics
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import pytest

from audit_log import AuditLogStore
from auth import AuthStore, MfaService, PasswordResetService, ResetDelivery, totp

from fixtures import FICTIONAL_USERS

USER, OLD_PASSWORD, ROLE = FICTIONAL_USERS[0]
OTHER = FICTIONAL_USERS[1][0]


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
def reset(auth_store, audit_log):
    return PasswordResetService(auth_store, audit_log=audit_log)


def _actions(audit_log):
    return [e.action for e in audit_log.get_all()]


def _serialized(audit_log):
    return json.dumps([asdict(e) for e in audit_log.get_all()], default=str)


# ---------------------------------------------------------------------------
# 1. a valid reset works end to end
# ---------------------------------------------------------------------------

def test_a_valid_reset_works_end_to_end(reset, auth_store, audit_log):
    delivery = reset.request_reset(USER)
    assert isinstance(delivery, ResetDelivery)
    assert delivery.reset_token in delivery.reset_link
    assert "never" in delivery.delivery_note.lower()

    assert reset.redeem_reset(delivery.reset_token, "a-brand-new-password") is True

    assert auth_store.login(USER, "a-brand-new-password") is not None
    assert auth_store.login(USER, OLD_PASSWORD) is None

    assert _actions(audit_log) == [
        "auth.password_reset.requested",
        "auth.password_reset.redeemed",
    ]


# ---------------------------------------------------------------------------
# 2. a reset token cannot be reused
# ---------------------------------------------------------------------------

def test_a_reset_token_cannot_be_reused(reset, auth_store, audit_log):
    delivery = reset.request_reset(USER)

    assert reset.redeem_reset(delivery.reset_token, "pw-one") is True
    assert reset.redeem_reset(delivery.reset_token, "pw-two") is False

    assert auth_store.login(USER, "pw-one") is not None
    assert auth_store.login(USER, "pw-two") is None

    failed = audit_log.get_all()[-1]
    assert failed.action == "auth.password_reset.redeem_failed"
    assert failed.inputs["reason"] == "used"


# ---------------------------------------------------------------------------
# 3. THE CRUCIAL ONE — a request for an unknown username is indistinguishable
# ---------------------------------------------------------------------------

def test_request_for_an_unknown_username_is_indistinguishable(reset, audit_log):
    real = reset.request_reset(USER)
    fake = reset.request_reset("ghost.user.42")

    # Identical response shape, both carry a real token.
    assert type(real) is type(fake)
    assert real.reset_token and fake.reset_token
    assert set(asdict(real)) == set(asdict(fake))
    assert real.delivery_note == fake.delivery_note

    # The ONLY difference anywhere is an internal audit field.
    ev_real, ev_fake = audit_log.get_all()[-2:]
    assert ev_real.action == ev_fake.action == "auth.password_reset.requested"
    assert ev_real.inputs["user_exists"] is True
    assert ev_fake.inputs["user_exists"] is False

    # Redeeming the fake token fails the same generic way as a garbage token.
    assert reset.redeem_reset(fake.reset_token, "x") is False
    assert reset.redeem_reset("not-even-a-token", "x") is False
    for e in audit_log.get_all()[-2:]:
        assert e.action == "auth.password_reset.redeem_failed"
        assert e.inputs["reason"] == "invalid"


def test_request_timing_does_not_depend_on_username_existence(reset):
    """Lenient smoke check: neither path does PBKDF2-scale (~50ms+) work, and
    they are within a wide factor of each other. The real guarantee is the
    no-existence-branch code path."""
    def median_seconds(username):
        samples = []
        for _ in range(25):
            t0 = time.perf_counter()
            reset.request_reset(username)
            samples.append(time.perf_counter() - t0)
        return statistics.median(samples)

    known = median_seconds(USER)
    unknown = median_seconds("nobody.here.at.all")

    assert known < 0.03 and unknown < 0.03           # no expensive per-branch work
    assert max(known, unknown) < 5 * min(known, unknown)  # same order of magnitude


# ---------------------------------------------------------------------------
# 4. resetting a password invalidates existing sessions
# ---------------------------------------------------------------------------

def test_reset_invalidates_all_existing_sessions(reset, auth_store, audit_log):
    s1 = auth_store.login(USER, OLD_PASSWORD)
    s2 = auth_store.login(USER, OLD_PASSWORD)
    assert auth_store.validate_token(s1) is not None
    assert auth_store.validate_token(s2) is not None

    delivery = reset.request_reset(USER)
    assert reset.redeem_reset(delivery.reset_token, "post-reset-pw") is True

    assert auth_store.validate_token(s1) is None
    assert auth_store.validate_token(s2) is None

    redeemed = [e for e in audit_log.get_all() if e.action == "auth.password_reset.redeemed"][-1]
    assert redeemed.output["sessions_invalidated"] == 2


# ---------------------------------------------------------------------------
# supporting
# ---------------------------------------------------------------------------

def test_expired_reset_token_is_rejected(reset):
    t0 = datetime(2026, 9, 7, 12, 0, 0, tzinfo=timezone.utc)
    delivery = reset.request_reset(USER, ttl_seconds=900, now=t0)
    assert reset.redeem_reset(delivery.reset_token, "x", now=t0 + timedelta(minutes=16)) is False


def test_raw_token_is_not_stored_and_neither_token_nor_password_is_logged(reset, audit_log, tmp_path):
    delivery = reset.request_reset(USER)
    reset.redeem_reset(delivery.reset_token, "MARKER-PASSWORD-9931")

    raw = sqlite3.connect(tmp_path / "auth.db")
    rows = str(raw.execute("SELECT * FROM password_reset_tokens").fetchall())
    raw.close()
    assert delivery.reset_token not in rows          # only sha256 stored

    blob = _serialized(audit_log)
    assert delivery.reset_token not in blob
    assert "MARKER-PASSWORD-9931" not in blob


def test_redeem_garbage_token_logs_a_generic_failure(reset, audit_log):
    assert reset.redeem_reset("garbage", "x") is False
    failed = audit_log.get_all()[-1]
    assert failed.action == "auth.password_reset.redeem_failed"
    assert failed.inputs["reason"] == "invalid"
    assert failed.actor == "unknown"


def test_reset_does_not_disable_mfa(reset, auth_store, audit_log):
    mfa = MfaService(auth_store, audit_log=audit_log)
    secret = mfa.enable(USER).secret

    delivery = reset.request_reset(USER)
    assert reset.redeem_reset(delivery.reset_token, "still-has-mfa") is True

    assert mfa.is_enabled(USER) is True
    # the new password + a valid TOTP code still logs in
    assert auth_store.login(USER, "still-has-mfa", totp_code=totp.generate_code(secret)) is not None


def test_last_reset_requested_at_is_recorded(reset, auth_store):
    t0 = datetime(2026, 9, 7, 9, 30, 0, tzinfo=timezone.utc)
    reset.request_reset(USER, now=t0)
    assert auth_store.last_reset_requested_at(USER) == t0.isoformat()


def test_a_second_request_supersedes_nothing_but_both_tokens_are_independent(reset, auth_store):
    first = reset.request_reset(USER)
    second = reset.request_reset(USER)
    assert first.reset_token != second.reset_token

    # Older token still works (until used/expired); using it burns only itself.
    assert reset.redeem_reset(first.reset_token, "from-first") is True
    assert reset.redeem_reset(second.reset_token, "from-second") is True
    assert auth_store.login(USER, "from-second") is not None
