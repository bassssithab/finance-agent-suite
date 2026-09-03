"""Tests for telegram_bot_photo.py — the photo → routed agent draft logic.

python-telegram-bot is not needed (the bot imports this module lazily). The
real agent runs and the Telegram photo download are verified manually — see
telegram_bot_prototype.py's docstring.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent
for _p in (ROOT / "platform" / "audit-log", ROOT / "platform" / "approvals"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from audit_log import AuditLogStore  # noqa: E402

import telegram_bot_photo as photo  # noqa: E402


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
