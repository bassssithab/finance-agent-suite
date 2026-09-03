import json
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import pytest

from audit_log import AuditLogStore
from auth import AuthStore
from telegram_link import (
    ChatAlreadyLinked,
    InvalidLinkCode,
    LinkCodeAlreadyUsed,
    LinkCodeExpired,
    NoActiveLink,
    TelegramLinkStore,
    UnknownUser,
)

from fixtures import ADMIN, CHAT_DANA, CHAT_FARAH, CHAT_SPARE, FICTIONAL_USERS


@pytest.fixture
def auth_store(tmp_path):
    s = AuthStore(tmp_path / "auth.db")
    for username, (password, role) in FICTIONAL_USERS.items():
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
    """Every test — old and new — leaves the hash chain valid."""
    yield
    assert audit_log.verify_chain().ok is True


@pytest.fixture
def store(tmp_path, audit_log, auth_store):
    s = TelegramLinkStore(tmp_path / "telegram_link.db", audit_log=audit_log, auth_store=auth_store)
    yield s
    s.close()


def _actions(audit_log):
    return [e.action for e in audit_log.get_all()]


def _serialized_events(audit_log):
    return json.dumps([asdict(e) for e in audit_log.get_all()], default=str)


# ---------------------------------------------------------------------------
# 1. A valid code links successfully.
# ---------------------------------------------------------------------------

def test_a_valid_code_links_successfully(store, audit_log):
    code = store.generate_link_code("dana.acme")
    link = store.redeem_link_code(code, CHAT_DANA)

    assert link.chat_id == CHAT_DANA
    assert link.username == "dana.acme"
    assert link.is_active

    resolved = store.resolve_chat_id(CHAT_DANA)
    assert resolved is not None
    assert resolved.username == "dana.acme"

    assert _actions(audit_log) == [
        "telegram_link.code_generated",
        "telegram_link.code_redeemed",
    ]


# ---------------------------------------------------------------------------
# 2. A valid code can never be redeemed twice.
# ---------------------------------------------------------------------------

def test_double_redemption_fails(store, audit_log):
    code = store.generate_link_code("dana.acme")
    store.redeem_link_code(code, CHAT_DANA)

    with pytest.raises(LinkCodeAlreadyUsed):
        store.redeem_link_code(code, CHAT_FARAH)

    # The second chat_id was never linked.
    assert store.resolve_chat_id(CHAT_FARAH) is None
    # The failed attempt is recorded as a security signal.
    assert _actions(audit_log)[-1] == "telegram_link.redeem_failed"
    assert audit_log.get_all()[-1].inputs["reason"] == "already_used"


# ---------------------------------------------------------------------------
# 3. An expired code fails.
# ---------------------------------------------------------------------------

def test_an_expired_code_fails(store, audit_log):
    issued = datetime(2026, 9, 3, 12, 0, 0, tzinfo=timezone.utc)
    code = store.generate_link_code("dana.acme", ttl_seconds=600, now=issued)

    with pytest.raises(LinkCodeExpired):
        store.redeem_link_code(code, CHAT_DANA, now=issued + timedelta(minutes=11))

    assert store.resolve_chat_id(CHAT_DANA) is None
    assert audit_log.get_all()[-1].action == "telegram_link.redeem_failed"
    assert audit_log.get_all()[-1].inputs["reason"] == "expired"


# ---------------------------------------------------------------------------
# 4. A chat_id links to only one user at a time — re-linking fails loudly.
# ---------------------------------------------------------------------------

def test_relink_without_revoke_fails_loudly(store):
    dana_code = store.generate_link_code("dana.acme")
    store.redeem_link_code(dana_code, CHAT_DANA)

    farah_code = store.generate_link_code("farah.globex")
    with pytest.raises(ChatAlreadyLinked):
        store.redeem_link_code(farah_code, CHAT_DANA)

    # Still linked to the original user, untouched.
    assert store.resolve_chat_id(CHAT_DANA).username == "dana.acme"
    # farah's code was not consumed — it can still be used elsewhere.
    store.redeem_link_code(farah_code, CHAT_FARAH)
    assert store.resolve_chat_id(CHAT_FARAH).username == "farah.globex"


# ---------------------------------------------------------------------------
# 5. A revoked link correctly fails lookup afterward.
# ---------------------------------------------------------------------------

def test_a_revoked_link_fails_lookup(store, audit_log):
    code = store.generate_link_code("dana.acme")
    store.redeem_link_code(code, CHAT_DANA)
    assert store.resolve_chat_id(CHAT_DANA) is not None

    store.revoke_link(CHAT_DANA, revoked_by=ADMIN)

    assert store.resolve_chat_id(CHAT_DANA) is None
    revoke_event = audit_log.get_all()[-1]
    assert revoke_event.action == "telegram_link.link_revoked"
    assert revoke_event.actor == ADMIN
    assert revoke_event.inputs["previous_username"] == "dana.acme"


# ---------------------------------------------------------------------------
# Supporting tests
# ---------------------------------------------------------------------------

def test_an_unknown_code_fails_loudly(store, audit_log):
    with pytest.raises(InvalidLinkCode):
        store.redeem_link_code("not-a-real-code", CHAT_DANA)
    failed = audit_log.get_all()[-1]
    assert failed.action == "telegram_link.redeem_failed"
    assert failed.inputs["reason"] == "invalid"
    assert failed.actor == "unknown"


def test_generate_for_unknown_user_raises(store):
    with pytest.raises(UnknownUser):
        store.generate_link_code("nobody.here")


def test_raw_code_is_never_in_the_audit_log(store, audit_log):
    code = store.generate_link_code("dana.acme")
    store.redeem_link_code(code, CHAT_DANA)
    store.revoke_link(CHAT_DANA, revoked_by=ADMIN)

    assert code not in _serialized_events(audit_log)


def test_generating_a_new_code_supersedes_the_previous_one(store):
    first = store.generate_link_code("dana.acme")
    second = store.generate_link_code("dana.acme")

    with pytest.raises(InvalidLinkCode):
        store.redeem_link_code(first, CHAT_DANA)

    store.redeem_link_code(second, CHAT_DANA)
    assert store.resolve_chat_id(CHAT_DANA).username == "dana.acme"


def test_re_link_after_revoke_succeeds(store, audit_log):
    store.redeem_link_code(store.generate_link_code("dana.acme"), CHAT_DANA)
    store.revoke_link(CHAT_DANA, revoked_by=ADMIN)

    # A fresh code for a different user can now claim the same chat_id.
    store.redeem_link_code(store.generate_link_code("evan.acme"), CHAT_DANA)

    assert store.resolve_chat_id(CHAT_DANA).username == "evan.acme"
    redeemed = [e for e in audit_log.get_all() if e.action == "telegram_link.code_redeemed"][-1]
    assert redeemed.output["superseded_revoked_link"] is True


def test_revoke_with_no_active_link_raises(store):
    with pytest.raises(NoActiveLink):
        store.revoke_link(CHAT_SPARE, revoked_by=ADMIN)


def test_double_revoke_raises(store):
    store.redeem_link_code(store.generate_link_code("dana.acme"), CHAT_DANA)
    store.revoke_link(CHAT_DANA, revoked_by=ADMIN)
    with pytest.raises(NoActiveLink):
        store.revoke_link(CHAT_DANA, revoked_by=ADMIN)


def test_resolve_chat_id_is_not_audited(store, audit_log):
    store.redeem_link_code(store.generate_link_code("dana.acme"), CHAT_DANA)
    before = len(audit_log.get_all())

    store.resolve_chat_id(CHAT_DANA)
    store.resolve_chat_id(CHAT_DANA)
    store.resolve_chat_id(CHAT_SPARE)

    assert len(audit_log.get_all()) == before


def test_normalization_tolerates_surrounding_whitespace(store):
    code = store.generate_link_code("dana.acme")
    store.redeem_link_code(f"  {code}\n", CHAT_DANA)
    assert store.resolve_chat_id(CHAT_DANA).username == "dana.acme"
