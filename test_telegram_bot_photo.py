"""Tests for telegram_bot_photo.py — the photo/document → routed agent draft
logic — plus the telegram_bot_prototype.on_document handler itself.

Most of this file needs no python-telegram-bot (telegram_bot_photo.py imports
it lazily, and doesn't need it at all). The on_document handler tests near
the bottom do import it (via telegram_bot_prototype._build_handlers, which
imports telegram.Update/ContextTypes/TelegramError lazily) — it's already a
dependency of telegram_bot_prototype.py (requirements-telegram.txt), just
exercised here with SimpleNamespace/AsyncMock stand-ins for update/context
instead of a real Application, bot token, or network connection. The real
agent runs and the real Telegram photo/document download are still verified
manually — see telegram_bot_prototype.py's docstring.
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

ROOT = Path(__file__).resolve().parent
for _p in (ROOT / "platform" / "audit-log", ROOT / "platform" / "approvals"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from audit_log import AuditLogStore  # noqa: E402

import telegram_bot_photo as photo  # noqa: E402
import telegram_bot_prototype as prototype  # noqa: E402


# ---------------------------------------------------------------------------
# 1. caption routing — deterministic
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "caption, expected",
    [
        ("my expense from lunch", "expense"),
        ("receipt", "expense"),
        ("Receipt for the taxi", "expense"),
        ("supplier invoice attached", "invoice"),
        ("purchase order", "invoice"),
        ("the bill from our vendor", "invoice"),
        ("expense invoice", None),          # conflicting keywords -> classify
        ("receipt for this purchase", None),
        ("", None),
        (None, None),
        ("here's a photo", None),
    ],
)
def test_route_by_caption(caption, expected):
    assert photo.route_by_caption(caption) == expected


# ---------------------------------------------------------------------------
# 2. vision classification — one Claude call (fake client)
# ---------------------------------------------------------------------------

class _FakeToolUse:
    type = "tool_use"

    def __init__(self, kind):
        self.input = {"kind": kind}


class _FakeResponse:
    def __init__(self, *, stop_reason="tool_use", content=None):
        self.stop_reason = stop_reason
        self.content = content or []


class _FakeClient:
    def __init__(self, response):
        self._response = response
        self.calls = []

    class _Messages:
        def __init__(self, outer):
            self._outer = outer

        def create(self, **kwargs):
            self._outer.calls.append(kwargs)
            return self._outer._response

    @property
    def messages(self):
        return _FakeClient._Messages(self)


@pytest.mark.parametrize(
    "kind, expected",
    [("expense_receipt", "expense"), ("purchase_invoice", "invoice"), ("unclear", "unclear")],
)
def test_classify_photo_maps_each_label(kind, expected):
    client = _FakeClient(_FakeResponse(content=[_FakeToolUse(kind)]))
    assert photo.classify_photo(client, b"\xff\xd8jpeg") == expected


def test_classify_photo_refusal_is_unclear():
    client = _FakeClient(_FakeResponse(stop_reason="refusal", content=[]))
    assert photo.classify_photo(client, b"\xff\xd8jpeg") == "unclear"


def test_classify_photo_no_tool_call_is_unclear():
    client = _FakeClient(_FakeResponse(content=[SimpleNamespace(type="text", text="hi")]))
    assert photo.classify_photo(client, b"\xff\xd8jpeg") == "unclear"


# ---------------------------------------------------------------------------
# 5. summaries — pure formatting over a run object
# ---------------------------------------------------------------------------

def _expense_run(*, passed=True, violations=(), vendor="Sample Cafe", amount="38.40"):
    return SimpleNamespace(
        draft=SimpleNamespace(
            receipt=SimpleNamespace(
                vendor=vendor, currency="USD", amount=amount,
                expense_category="meals", date="2026-09-01",
            ),
            compliance=SimpleNamespace(passed=passed, violations=list(violations)),
        ),
        approval_request=SimpleNamespace(id=1),
        extraction=SimpleNamespace(refused=False, parse_error=None),
    )


def _invoice_run(*, ok=True, difference="0.00", suggestions=()):
    return SimpleNamespace(
        draft=SimpleNamespace(
            invoice=SimpleNamespace(
                vendor_name="Globex Supplies", currency="USD", grand_total="1250.00",
                invoice_number="INV-9", invoice_date="2026-08-20",
            ),
            sanity_check=SimpleNamespace(ok=ok, difference=difference),
            gl_suggestions=list(suggestions),
        ),
        approval_request=SimpleNamespace(id=1),
        extraction=SimpleNamespace(refused=False, parse_error=None),
    )


def test_summarize_expense_within_policy():
    text = photo.summarize_expense(_expense_run(passed=True), "dana.acme")
    assert "🧾 Expense receipt — Sample Cafe" in text
    assert "USD 38.40" in text
    assert "✅ within policy" in text
    assert "dana.acme's draft" in text
    assert "Not final until a human approves" in text


def test_summarize_expense_with_violations():
    v = SimpleNamespace(detail="meal $182.50 over the $75 per-meal cap")
    text = photo.summarize_expense(_expense_run(passed=False, violations=[v]), "dana.acme")
    assert "⚠️ meal $182.50 over the $75 per-meal cap" in text


def test_summarize_invoice_totals_tie():
    s = SimpleNamespace(account_code="6000", description="widgets")
    text = photo.summarize_invoice(_invoice_run(ok=True, suggestions=[s]), "farah.globex")
    assert "📄 Purchase invoice — Globex Supplies" in text
    assert "✅ line items tie to the grand total" in text
    assert "1 line(s) suggested" in text
    assert "farah.globex's draft" in text


def test_summarize_invoice_mismatch_and_uncoded():
    s = SimpleNamespace(account_code=None, description="mystery line")
    text = photo.summarize_invoice(_invoice_run(ok=False, difference="12.00", suggestions=[s]), "farah.globex")
    assert "⚠️ off by 12.00" in text
    assert "1 uncoded (not in the chart)" in text


def test_summary_reports_extraction_failure():
    run = SimpleNamespace(
        draft=None,
        extraction=SimpleNamespace(refused=False, parse_error="blurry image"),
    )
    assert "blurry image" in photo.summarize_expense(run, "dana.acme")
    run.extraction = SimpleNamespace(refused=True, refusal_category="frontier_llm")
    assert "declined it" in photo.summarize_invoice(run, "dana.acme")


# ---------------------------------------------------------------------------
# orchestration — process_photo (agent runners monkeypatched)
# ---------------------------------------------------------------------------

@pytest.fixture
def audit_log(tmp_path):
    log = AuditLogStore(tmp_path / "audit.db")
    yield log
    log.close()


def test_process_photo_caption_route_runs_expense_and_records(monkeypatch, audit_log):
    run = _expense_run()
    monkeypatch.setattr(photo, "run_expense", lambda *a, **k: run)
    monkeypatch.setattr(photo, "run_invoice", lambda *a, **k: pytest.fail("wrong agent"))

    reply = photo.process_photo(
        client=object(), jpeg_bytes=b"x", caption="my expense",
        username="dana.acme", chat_id=42, persistent_audit_log=audit_log,
    )

    assert "🧾 Expense receipt" in reply
    events = audit_log.get_all()
    assert [e.action for e in events] == ["telegram_bot.photo_routed"]
    e = events[-1]
    assert e.actor == "dana.acme"
    assert e.inputs["routing"] == "caption"
    assert e.inputs["doc_type"] == "expense"
    assert e.inputs["agent"] == "expense-agent"
    assert audit_log.verify_chain().ok is True


def test_process_photo_no_caption_uses_vision_then_routes(monkeypatch, audit_log):
    monkeypatch.setattr(photo, "classify_photo", lambda *a, **k: "invoice")
    run = _invoice_run()
    monkeypatch.setattr(photo, "run_invoice", lambda *a, **k: run)

    reply = photo.process_photo(
        client=object(), jpeg_bytes=b"x", caption=None,
        username="farah.globex", chat_id=7, persistent_audit_log=audit_log,
    )

    assert "📄 Purchase invoice" in reply
    assert audit_log.get_all()[-1].inputs["routing"] == "vision"


def test_process_photo_unclear_asks_for_a_caption_and_records_nothing(monkeypatch, audit_log):
    monkeypatch.setattr(photo, "classify_photo", lambda *a, **k: "unclear")

    reply = photo.process_photo(
        client=object(), jpeg_bytes=b"x", caption="   ",
        username="dana.acme", chat_id=1, persistent_audit_log=audit_log,
    )

    assert "resend the photo with a caption" in reply
    assert audit_log.get_all() == []


def test_process_photo_classify_api_failure_is_friendly(monkeypatch, audit_log):
    import anthropic

    def boom(*a, **k):
        raise anthropic.APIConnectionError(request=None)

    monkeypatch.setattr(photo, "classify_photo", boom)

    reply = photo.process_photo(
        client=object(), jpeg_bytes=b"x", caption=None,
        username="dana.acme", chat_id=1, persistent_audit_log=audit_log,
    )

    assert "image service failed" in reply
    assert audit_log.get_all() == []


def test_process_photo_agent_failure_is_friendly(monkeypatch, audit_log):
    import anthropic

    def boom(*a, **k):
        raise anthropic.APIConnectionError(request=None)

    monkeypatch.setattr(photo, "run_expense", boom)

    reply = photo.process_photo(
        client=object(), jpeg_bytes=b"x", caption="expense receipt",
        username="dana.acme", chat_id=1, persistent_audit_log=audit_log,
    )

    assert "couldn't process that photo" in reply
    assert audit_log.get_all() == []


# ---------------------------------------------------------------------------
# reject_document — pre-download gate (pure, no client/network)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "media_type, file_size",
    [
        ("application/pdf", 10 * 1024 * 1024),   # exactly at the PDF cap
        ("image/png", 5 * 1024 * 1024),          # exactly at the image cap
        ("image/jpeg", 1024),
    ],
)
def test_reject_document_accepts_supported_types_at_or_under_the_cap(
    media_type, file_size, audit_log
):
    reply = photo.reject_document(
        audit_log, media_type=media_type, file_size=file_size,
        username="dana.acme", chat_id=1,
    )
    assert reply is None
    assert audit_log.get_all() == []


def test_reject_document_rejects_oversize_pdf(audit_log):
    reply = photo.reject_document(
        audit_log, media_type="application/pdf", file_size=10 * 1024 * 1024 + 1,
        username="dana.acme", chat_id=1,
    )
    assert reply is not None and "too large" in reply
    event = audit_log.get_all()[-1]
    assert event.action == "telegram_bot.document_rejected"
    assert event.output["reason"] == "content_too_large"
    assert event.inputs["media_type"] == "application/pdf"


def test_reject_document_rejects_oversize_image(audit_log):
    reply = photo.reject_document(
        audit_log, media_type="image/png", file_size=5 * 1024 * 1024 + 1,
        username="dana.acme", chat_id=1,
    )
    assert reply is not None and "too large" in reply
    assert audit_log.get_all()[-1].output["reason"] == "content_too_large"


def test_reject_document_rejects_unsupported_media_type(audit_log):
    reply = photo.reject_document(
        audit_log, media_type="application/msword", file_size=1024,
        username="dana.acme", chat_id=1,
    )
    assert reply is not None and "isn't supported" in reply
    event = audit_log.get_all()[-1]
    assert event.action == "telegram_bot.document_rejected"
    assert event.output["reason"] == "unsupported_media_type"
    assert audit_log.verify_chain().ok is True


# ---------------------------------------------------------------------------
# process_document — orchestration (agent runners monkeypatched), mirrors
# the process_photo tests above
# ---------------------------------------------------------------------------

def test_process_document_pdf_caption_route_runs_invoice_and_records(monkeypatch, audit_log):
    run = _invoice_run()
    monkeypatch.setattr(photo, "run_invoice_document", lambda *a, **k: run)
    monkeypatch.setattr(photo, "run_expense_document", lambda *a, **k: pytest.fail("wrong agent"))

    reply = photo.process_document(
        client=object(), content_bytes=b"%PDF-1.4", media_type="application/pdf",
        caption="supplier invoice", username="farah.globex", chat_id=42,
        persistent_audit_log=audit_log,
    )

    assert "📄 Purchase invoice" in reply
    events = audit_log.get_all()
    assert [e.action for e in events] == ["telegram_bot.photo_routed"]
    e = events[-1]
    assert e.inputs["routing"] == "caption"
    assert e.inputs["doc_type"] == "invoice"
    assert e.output["media_type"] == "application/pdf"
    assert audit_log.verify_chain().ok is True


def test_process_document_png_sent_as_a_file_runs_expense(monkeypatch, audit_log):
    run = _expense_run()
    monkeypatch.setattr(photo, "run_expense_document", lambda *a, **k: run)
    monkeypatch.setattr(photo, "run_invoice_document", lambda *a, **k: pytest.fail("wrong agent"))

    reply = photo.process_document(
        client=object(), content_bytes=b"\x89PNG", media_type="image/png",
        caption="my receipt", username="dana.acme", chat_id=7,
        persistent_audit_log=audit_log,
    )

    assert "🧾 Expense receipt" in reply
    e = audit_log.get_all()[-1]
    assert e.inputs["doc_type"] == "expense"
    assert e.output["media_type"] == "image/png"


def test_process_document_no_caption_uses_vision_then_routes(monkeypatch, audit_log):
    monkeypatch.setattr(photo, "classify_document", lambda *a, **k: "invoice")
    run = _invoice_run()
    monkeypatch.setattr(photo, "run_invoice_document", lambda *a, **k: run)

    reply = photo.process_document(
        client=object(), content_bytes=b"%PDF-1.4", media_type="application/pdf",
        caption=None, username="farah.globex", chat_id=7,
        persistent_audit_log=audit_log,
    )

    assert "📄 Purchase invoice" in reply
    assert audit_log.get_all()[-1].inputs["routing"] == "vision"


# ---------------------------------------------------------------------------
# on_document — the actual Telegram handler, via
# telegram_bot_prototype._build_handlers (SimpleNamespace/AsyncMock
# stand-ins for update/context/stores — no real Application, bot token, or
# network connection; see that function's docstring for why it's split out)
# ---------------------------------------------------------------------------

def _fake_stores(audit_log, *, linked_username=None):
    user = SimpleNamespace(username=linked_username) if linked_username else None
    return SimpleNamespace(
        links=SimpleNamespace(resolve_chat_id=lambda chat_id: user),
        audit_log=audit_log,
    )


def _fake_update(*, chat_id, mime_type="application/pdf", file_size=1024, file_id="file-1", caption=None):
    document = SimpleNamespace(
        mime_type=mime_type, file_size=file_size, file_id=file_id, file_name="upload"
    )
    message = SimpleNamespace(document=document, caption=caption, reply_text=AsyncMock())
    return SimpleNamespace(effective_chat=SimpleNamespace(id=chat_id), message=message)


def _fake_context(*, file_bytes=b"content"):
    tg_file = SimpleNamespace(download_as_bytearray=AsyncMock(return_value=bytearray(file_bytes)))
    bot = SimpleNamespace(get_file=AsyncMock(return_value=tg_file))
    return SimpleNamespace(bot=bot)


def test_on_document_unlinked_chat_gets_instructions_and_never_downloads(monkeypatch, audit_log):
    monkeypatch.setattr(
        photo, "process_document",
        lambda *a, **k: pytest.fail("should not process an unlinked chat's document"),
    )
    stores = _fake_stores(audit_log, linked_username=None)
    handlers = prototype._build_handlers(stores, photo)
    update = _fake_update(chat_id=1)
    context = _fake_context()

    asyncio.run(handlers.on_document(update, context))

    update.message.reply_text.assert_awaited_once_with(prototype._INSTRUCTIONS)
    context.bot.get_file.assert_not_called()
    assert audit_log.get_all() == []


def test_on_document_oversize_is_rejected_before_download(monkeypatch, audit_log):
    monkeypatch.setattr(prototype, "anthropic_key", lambda: "fake-key")
    monkeypatch.setattr(
        photo, "process_document",
        lambda *a, **k: pytest.fail("should not process a rejected document"),
    )
    stores = _fake_stores(audit_log, linked_username="dana.acme")
    handlers = prototype._build_handlers(stores, photo)
    update = _fake_update(
        chat_id=1, mime_type="application/pdf", file_size=10 * 1024 * 1024 + 1,
    )
    context = _fake_context()

    asyncio.run(handlers.on_document(update, context))

    reply_text = update.message.reply_text.call_args.args[0]
    assert "too large" in reply_text
    context.bot.get_file.assert_not_called()
    event = audit_log.get_all()[-1]
    assert event.action == "telegram_bot.document_rejected"
    assert event.output["reason"] == "content_too_large"


def test_on_document_small_pdf_downloads_once_and_processes(monkeypatch, audit_log):
    monkeypatch.setattr(prototype, "anthropic_key", lambda: "fake-key")
    calls = []

    def fake_process_document(**kwargs):
        calls.append(kwargs)
        return "✅ processed"

    monkeypatch.setattr(photo, "process_document", fake_process_document)
    stores = _fake_stores(audit_log, linked_username="farah.globex")
    handlers = prototype._build_handlers(stores, photo)
    update = _fake_update(
        chat_id=7, mime_type="application/pdf", file_size=1024, file_id="file-42",
    )
    context = _fake_context(file_bytes=b"%PDF-1.4 small invoice")

    asyncio.run(handlers.on_document(update, context))

    context.bot.get_file.assert_awaited_once_with("file-42")
    assert len(calls) == 1
    assert calls[0]["media_type"] == "application/pdf"
    assert calls[0]["username"] == "farah.globex"
    assert calls[0]["content_bytes"] == b"%PDF-1.4 small invoice"
    assert update.message.reply_text.await_args.args[0] == "✅ processed"
