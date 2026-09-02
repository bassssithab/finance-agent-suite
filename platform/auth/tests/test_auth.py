import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from auth import AuthStore, Role, UserExists

from fixtures import FICTIONAL_USERS

# A known-good fixture account used across the happy-path tests.
GOOD_USER, GOOD_PASSWORD, GOOD_ROLE = FICTIONAL_USERS[0]


@pytest.fixture
def store(tmp_path):
    s = AuthStore(tmp_path / "auth.db")
    for username, password, role in FICTIONAL_USERS:
        s.create_user(username, password, role)
    yield s
    s.close()


def test_successful_login_returns_token(store):
    token = store.login(GOOD_USER, GOOD_PASSWORD)

    assert isinstance(token, str)
    assert token  # non-empty


def test_wrong_password_is_rejected(store):
    assert store.verify_login(GOOD_USER, "not-the-password") is False
    assert store.login(GOOD_USER, "not-the-password") is None


def test_unknown_username_is_rejected_identically_to_wrong_password(store):
    unknown = store.verify_login("nobody.here", "whatever")
    wrong_pw = store.verify_login(GOOD_USER, "whatever")

    # Same result, no exception distinguishes the two: a caller cannot learn
    # whether "nobody.here" is a real username.
    assert unknown is False
    assert wrong_pw is False
    assert store.login("nobody.here", "whatever") is None


def test_password_is_never_stored_in_plaintext(store, tmp_path):
    raw = sqlite3.connect(tmp_path / "auth.db")
    rows = raw.execute("SELECT username, password_hash, role, created_at FROM users").fetchall()
    raw.close()

    stored_blob = " ".join(str(cell) for row in rows for cell in row)
    for _, password, _ in FICTIONAL_USERS:
        assert password not in stored_blob

    for _, password_hash, _, _ in rows:
        assert password_hash.startswith("pbkdf2_sha256$")


def test_valid_token_resolves_to_its_user(store):
    token = store.login(GOOD_USER, GOOD_PASSWORD)

    user = store.validate_token(token)

    assert user is not None
    assert user.username == GOOD_USER
    assert user.role == GOOD_ROLE
    assert isinstance(user.role, Role)


def test_expired_token_is_rejected(store):
    issued = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)
    token = store.login(GOOD_USER, GOOD_PASSWORD, ttl_seconds=60, now=issued)

    # Still valid a few seconds later...
    assert store.validate_token(token, now=issued + timedelta(seconds=30)) is not None
    # ...expired an hour later.
    assert store.validate_token(token, now=issued + timedelta(hours=1)) is None


def test_garbage_or_unknown_token_is_rejected(store):
    assert store.validate_token("") is None
    assert store.validate_token("not-a-real-token") is None
    assert store.validate_token("x" * 200) is None


def test_logout_invalidates_token(store):
    token = store.login(GOOD_USER, GOOD_PASSWORD)
    assert store.validate_token(token) is not None

    store.logout(token)

    assert store.validate_token(token) is None


def test_duplicate_username_raises(store):
    with pytest.raises(UserExists):
        store.create_user(GOOD_USER, "a-different-password", Role.PREPARER)


def test_two_logins_issue_distinct_valid_tokens(store):
    first = store.login(GOOD_USER, GOOD_PASSWORD)
    second = store.login(GOOD_USER, GOOD_PASSWORD)

    assert first != second
    assert store.validate_token(first) is not None
    assert store.validate_token(second) is not None


def test_raw_token_is_not_stored_at_rest(store, tmp_path):
    token = store.login(GOOD_USER, GOOD_PASSWORD)

    raw = sqlite3.connect(tmp_path / "auth.db")
    session_rows = raw.execute("SELECT token_hash, username FROM sessions").fetchall()
    raw.close()

    assert session_rows
    for token_hash, _ in session_rows:
        assert token_hash != token  # only the sha256 is persisted
