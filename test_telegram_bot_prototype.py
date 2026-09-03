"""Routing-logic tests for telegram_bot_prototype.py.

Imports only the pure helpers (route_message / redeem_reply); python-telegram-bot
is imported lazily inside cmd_run(), so these run without it installed. Uses a
real temp TelegramLinkStore — no Telegram, no network.
"""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
for _p in (
    ROOT / "platform" / "telegram-link",
    ROOT / "platform" / "auth",
    ROOT / "platform" / "audit-log",
):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from audit_log import AuditLogStore  # noqa: E402
from auth import AuthStore, Role  # noqa: E402
from telegram_link import TelegramLinkStore  # noqa: E402

import telegram_bot_prototype as bot  # noqa: E402

CHAT = 555001
OTHER_CHAT = 555002


@pytest.fixture
def links(tmp_path):
    auth = AuthStore(tmp_path / "auth.db")
    auth.create_user("dana.acme", "pw-placeholder", Role.APPROVER)
    auth.create_user("farah.globex", "pw-placeholder-2", Role.REVIEWER)
    audit = AuditLogStore(tmp_path / "audit.db")
    store = TelegramLinkStore(tmp_path / "link.db", audit_log=audit, auth_store=auth)
    yield store
    store.close()
    audit.close()
    auth.close()


# ---------------------------------------------------------------------------
# (3) unlinked + not a code -> instructions
# ---------------------------------------------------------------------------

def test_unlinked_non_code_gets_instructions(links):
    assert bot.route_message(links, CHAT, "hi, please help me link") == bot._INSTRUCTIONS
    assert bot.route_message(links, CHAT, "") == bot._INSTRUCTIONS


# ---------------------------------------------------------------------------
# (2) unlinked + a code -> redeem, friendly confirmation
# ---------------------------------------------------------------------------

def test_unlinked_valid_code_links_and_confirms(links):
    code = links.generate_link_code("dana.acme")
    reply = bot.route_message(links, CHAT, code)

    assert "Linked" in reply and "dana.acme" in reply
    assert "don't read message content" in reply
    assert links.resolve_chat_id(CHAT).username == "dana.acme"


def test_unlinked_already_used_code_is_friendly(links):
    code = links.generate_link_code("dana.acme")
    bot.route_message(links, CHAT, code)                     # consume it here
    reply = bot.route_message(links, OTHER_CHAT, code)       # reuse elsewhere

    assert "already been used" in reply
    assert links.resolve_chat_id(OTHER_CHAT) is None


def test_unlinked_expired_code_is_friendly(links):
    code = links.generate_link_code("dana.acme", ttl_seconds=-1)  # already expired
    reply = bot.route_message(links, CHAT, code)

    assert "expired" in reply
    assert links.resolve_chat_id(CHAT) is None


def test_unlinked_invalid_but_code_shaped_text_is_friendly(links):
    reply = bot.route_message(links, CHAT, "abcdef123456")  # 12 chars, not a real code

    assert "doesn't match any active linking code" in reply
    assert links.resolve_chat_id(CHAT) is None


# ---------------------------------------------------------------------------
# (4) already linked -> identity confirmation, no content processing
# ---------------------------------------------------------------------------

def test_linked_chat_gets_identity_confirmation(links):
    links.redeem_link_code(links.generate_link_code("dana.acme"), CHAT)

    reply = bot.route_message(links, CHAT, "here is a photo of my lunch receipt")

    assert reply == (
        "Hi dana.acme, message received — I'm not processing content yet, "
        "just confirming who you are."
    )


def test_linked_chat_sending_a_code_still_just_gets_identity_confirmation(links):
    """route_message checks 'is this chat linked?' first (spec step 4), so a
    linked chat that happens to send a code text still gets the identity
    reply — the code is never redeemed."""
    links.redeem_link_code(links.generate_link_code("dana.acme"), CHAT)
    farah_code = links.generate_link_code("farah.globex")

    reply = bot.route_message(links, CHAT, farah_code)

    assert "confirming who you are" in reply
    assert links.resolve_chat_id(CHAT).username == "dana.acme"  # unchanged
    # farah's code was never touched — still redeemable for another chat.
    links.redeem_link_code(farah_code, OTHER_CHAT)
    assert links.resolve_chat_id(OTHER_CHAT).username == "farah.globex"


def test_redeem_reply_refuses_a_code_for_an_already_linked_chat(links):
    """The /start <code> deep-link path calls redeem_reply directly. A code
    for an already-linked chat is an expected case, not a bug: friendly
    refusal, original link untouched."""
    links.redeem_link_code(links.generate_link_code("dana.acme"), CHAT)
    farah_code = links.generate_link_code("farah.globex")

    reply = bot.redeem_reply(links, CHAT, farah_code)

    assert "already linked to another account" in reply
    assert links.resolve_chat_id(CHAT).username == "dana.acme"


# ---------------------------------------------------------------------------
# the bot reuses telegram-link's audit logging (nothing new invented)
# ---------------------------------------------------------------------------

def test_redeem_through_the_bot_is_audited(links):
    bot.route_message(links, CHAT, links.generate_link_code("dana.acme"))
    bot.route_message(links, OTHER_CHAT, "abcdef123456")  # a failed attempt

    actions = [e.action for e in links.audit_log.get_all()]
    assert "telegram_link.code_redeemed" in actions
    assert "telegram_link.redeem_failed" in actions
    assert links.audit_log.verify_chain().ok is True


# ---------------------------------------------------------------------------
# `run` needs a token; it must never be hardcoded
# ---------------------------------------------------------------------------

def test_run_without_a_token_exits_nonzero_before_touching_telegram(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setattr(bot, "load_dotenv", lambda *a, **k: None)  # ignore the repo .env

    # Returns 1 (prints guidance) without importing telegram or opening stores.
    assert bot.main(["run"]) == 1


def test_no_token_is_hardcoded_in_the_source():
    src = (ROOT / "telegram_bot_prototype.py").read_text()
    assert "TELEGRAM_BOT_TOKEN" in src
    assert "os.environ" in src
    # a Telegram bot token is "<digits>:<35 urlsafe chars>"
    import re
    assert not re.search(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b", src)


# ---------------------------------------------------------------------------
# the routing path must not pull in python-telegram-bot
# ---------------------------------------------------------------------------

def test_routing_does_not_import_python_telegram_bot():
    result = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, '.'); "
         "import telegram_bot_prototype as b; b.route_message; "
         "assert 'telegram' not in sys.modules, 'PTB imported at module load'"],
        cwd=str(ROOT), capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
