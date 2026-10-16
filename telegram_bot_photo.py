"""Photo → routed agent draft, for telegram_bot_prototype.py (LOCAL-ONLY).

When a LINKED user sends a photo, the bot:

  1. tries plain keyword routing on the caption (deterministic, rule #4):
       "expense" / "receipt"            -> expense-agent
       "invoice" / "purchase" / "bill"  -> ap-agent
     exactly one set matches -> route; zero, both, or empty -> step 2
  2. one Claude vision call classifies the image as expense_receipt /
     purchase_invoice / unclear; "unclear" -> ask the user to resend with a
     caption, never guess
  3. runs the routed agent EXACTLY as app.py calls it (FileDocumentConnector
     over a temp dir, a cached KnowledgeBase, fresh in-memory audit/approval
     stores), with the linked user as `preparer`
  4. the agent submits its draft through platform/approvals
  5. one durable `telegram_bot.photo_routed` event is written to the bot's
     persistent hash-chained audit log, and a plain-text summary is returned

Stops at drafted + submitted. No Zoho, no ERP write.

No ANTHROPIC_API_KEY -> the bot handles that before calling in here; any API
failure here is caught and turned into a friendly message, never a crash.
"""

import importlib.util
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import anthropic

_ROOT = Path(__file__).resolve().parent
_PLATFORM = _ROOT / "platform"

for _p in (
    _PLATFORM / "connectors",
    _PLATFORM / "knowledge",
    _PLATFORM / "approvals",
    _PLATFORM / "audit-log",
    _ROOT / "agents" / "ap-agent",
    _ROOT / "agents" / "expense-agent",
):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from ap_agent import process_invoice  # noqa: E402
from ap_agent.extraction import DEFAULT_MODEL as _MODEL  # noqa: E402  (shared with expense-agent)
from approvals import ApprovalQueue  # noqa: E402
from audit_log import AuditEvent, AuditLogStore  # noqa: E402
from connectors import (  # noqa: E402
    DEFAULT_MAX_IMAGE_BYTES,
    DEFAULT_MAX_PDF_BYTES,
    ConnectorParseError,
    ContentTooLargeError,
    FileDocumentConnector,
    UnsupportedMediaTypeError,
    build_content_block,
)
from expense_agent import check_receipt_policy_compliance  # noqa: E402
from knowledge import KnowledgeBase  # noqa: E402

_EXPENSE_KEYWORDS = ("expense", "receipt")
_INVOICE_KEYWORDS = ("invoice", "purchase", "bill")

_NOT_FINAL = "Not final until a human approves. (No ERP write.)"

# A Telegram *document* message (as opposed to a compressed photo) carries its
# own mime_type/file_name, so unlike photos (always re-compressed to JPEG by
# Telegram) a document could be a PDF, or a PNG/JPEG sent "as a file" instead
# of as a photo. This is the same set connectors.build_content_block supports
# — kept as its own map here (extension, not media type, is what a filename
# written to disk needs) rather than re-deriving it from connectors' private
# extension table.
_SUPPORTED_DOCUMENT_MEDIA_TYPES = {
    "application/pdf": ".pdf",
    "image/png": ".png",
    "image/jpeg": ".jpg",
}


# --------------------------------------------------------------------------
# 1. caption routing — deterministic, no LLM
# --------------------------------------------------------------------------
def route_by_caption(caption):
    """-> "expense" | "invoice" | None.

    None means: missing / empty caption, no keyword, or a caption with
    conflicting keywords — all of which fall through to vision classification
    rather than being guessed at.
    """
    text = (caption or "").lower()
    is_expense = any(k in text for k in _EXPENSE_KEYWORDS)
    is_invoice = any(k in text for k in _INVOICE_KEYWORDS)
    if is_expense and not is_invoice:
        return "expense"
    if is_invoice and not is_expense:
        return "invoice"
    return None


# --------------------------------------------------------------------------
# 2. vision classification — one Claude call
# --------------------------------------------------------------------------
_CLASSIFY_TOOL = {
    "name": "classify_document",
    "description": "Record which kind of accounting document the image shows.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "kind": {
                "type": "string",
                "enum": ["expense_receipt", "purchase_invoice", "unclear"],
                "description": (
                    "expense_receipt = a store / restaurant / travel receipt an "
                    "employee would claim as an expense. purchase_invoice = a "
                    "supplier bill or invoice the company owes. unclear = you "
                    "cannot confidently tell, or it is neither."
                ),
            }
        },
        "required": ["kind"],
    },
}
_CLASSIFY_SYSTEM = (
    "You classify a single image as one of exactly three kinds of accounting "
    "document by calling classify_document once. If you are not confident, "
    "choose 'unclear' rather than guessing."
)
_KIND_TO_ROUTE = {
    "expense_receipt": "expense",
    "purchase_invoice": "invoice",
    "unclear": "unclear",
}


def _classify(client, content_bytes, media_type) -> str:
    """-> "expense" | "invoice" | "unclear".

    Shared by classify_photo (always image/jpeg) and classify_document (any
    media type build_content_block supports). Raises anthropic.AnthropicError
    on API failure, or ContentTooLargeError / UnsupportedMediaTypeError if
    content_bytes/media_type turn out not to fit (defense in depth — a
    document's caller should have already checked this via reject_document
    before ever downloading).
    """
    content_block = build_content_block(content_bytes, media_type)
    response = client.messages.create(
        model=_MODEL,
        max_tokens=256,
        system=_CLASSIFY_SYSTEM,
        output_config={"effort": "low"},
        tools=[_CLASSIFY_TOOL],
        tool_choice={"type": "tool", "name": "classify_document"},
        messages=[{
            "role": "user",
            "content": [content_block, {"type": "text", "text": "Classify this document."}],
        }],
    )
    if response.stop_reason == "refusal":
        return "unclear"
    tool_use = next(
        (b for b in response.content if getattr(b, "type", None) == "tool_use"), None
    )
    if tool_use is None:
        return "unclear"
    return _KIND_TO_ROUTE.get(tool_use.input.get("kind"), "unclear")


def classify_photo(client, jpeg_bytes) -> str:
    """-> "expense" | "invoice" | "unclear". Raises anthropic.AnthropicError on API failure."""
    return _classify(client, jpeg_bytes, "image/jpeg")


def classify_document(client, content_bytes, media_type) -> str:
    """-> "expense" | "invoice" | "unclear". Same as classify_photo, for any
    media type build_content_block supports (PDF included)."""
    return _classify(client, content_bytes, media_type)


# --------------------------------------------------------------------------
# 3. run the routed agent — exactly the shape app.py uses
# --------------------------------------------------------------------------
_KB_CACHE: dict[str, KnowledgeBase] = {}


def _all_documents(agent_dir: str):
    """Path-load agents/<dir>/evals/fixtures.py's ALL_DOCUMENTS (app.py's trick)."""
    path = _ROOT / "agents" / agent_dir / "evals" / "fixtures.py"
    name = f"_tgbot_{agent_dir.replace('-', '_')}_fixtures"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.ALL_DOCUMENTS


def _knowledge_base(agent_dir: str) -> KnowledgeBase:
    if agent_dir not in _KB_CACHE:
        kb = KnowledgeBase()
        kb.ingest(_all_documents(agent_dir))
        _KB_CACHE[agent_dir] = kb
    return _KB_CACHE[agent_dir]


def _fresh_stores():
    """A throwaway in-memory audit log + approval queue for one agent run
    (app.py's new_stores() pattern). The persistent bot audit log is separate."""
    audit_log = AuditLogStore(":memory:")
    approval_queue = ApprovalQueue(":memory:", audit_log)
    return audit_log, approval_queue


def _run_agent(agent_fn, kb_name, content_bytes, filename, client, username):
    """Shared by run_expense/run_invoice (always telegram_photo.jpg) and
    run_expense_document/run_invoice_document (filename extension matches the
    document's real media type): fresh stores + a temp folder +
    FileDocumentConnector + the agent call + store cleanup — app.py's
    new_stores()/FileDocumentConnector pattern, run once per Telegram upload."""
    audit_log, approval_queue = _fresh_stores()
    kb = _knowledge_base(kb_name)
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / filename).write_bytes(content_bytes)
        connector = FileDocumentConnector(
            source_system="telegram_bot_prototype", folder=tmp
        )
        try:
            return agent_fn(
                document_id=filename,
                document_connector=connector,
                knowledge_base=kb,
                audit_log=audit_log,
                approval_queue=approval_queue,
                client=client,
                preparer=username,
            )
        finally:
            audit_log.close()
            approval_queue.close()


def run_expense(client, jpeg_bytes, username):
    return _run_agent(
        check_receipt_policy_compliance, "expense-agent",
        jpeg_bytes, "telegram_photo.jpg", client, username,
    )


def run_invoice(client, jpeg_bytes, username):
    return _run_agent(
        process_invoice, "ap-agent",
        jpeg_bytes, "telegram_photo.jpg", client, username,
    )


def run_expense_document(client, content_bytes, media_type, username):
    filename = "telegram_document" + _SUPPORTED_DOCUMENT_MEDIA_TYPES[media_type]
    return _run_agent(
        check_receipt_policy_compliance, "expense-agent",
        content_bytes, filename, client, username,
    )


def run_invoice_document(client, content_bytes, media_type, username):
    filename = "telegram_document" + _SUPPORTED_DOCUMENT_MEDIA_TYPES[media_type]
    return _run_agent(
        process_invoice, "ap-agent",
        content_bytes, filename, client, username,
    )


# --------------------------------------------------------------------------
# 4 + 5. durable record + plain-text summary
# --------------------------------------------------------------------------
def _extraction_failure_text(extraction, noun: str) -> str:
    if extraction.refused:
        return (
            f"I couldn't read that photo — the vision step declined it "
            f"(category: {extraction.refusal_category}). Nothing was submitted."
        )
    return (
        f"I couldn't read that {noun} clearly ({extraction.parse_error}). "
        f"Try a flatter, better-lit photo. Nothing was submitted."
    )


def summarize_expense(run, username: str) -> str:
    if run.draft is None:
        return _extraction_failure_text(run.extraction, "receipt")
    r = run.draft.receipt
    c = run.draft.compliance
    policy = (
        "✅ within policy" if c.passed
        else "⚠️ " + "; ".join(v.detail for v in c.violations)
    )
    return (
        f"🧾 Expense receipt — {r.vendor or '—'}, {r.currency} {r.amount} · "
        f"{r.expense_category or '—'} · {r.date or '—'}\n"
        f"Policy: {policy}\n"
        f"Submitted for approval as {username}'s draft. {_NOT_FINAL}"
    )


def summarize_invoice(run, username: str) -> str:
    if run.draft is None:
        return _extraction_failure_text(run.extraction, "invoice")
    inv = run.draft.invoice
    sc = run.draft.sanity_check
    totals = (
        "✅ line items tie to the grand total" if sc.ok
        else f"⚠️ off by {sc.difference}"
    )
    n = len(run.draft.gl_suggestions)
    uncoded = sum(1 for s in run.draft.gl_suggestions if s.account_code is None)
    coding = f"{n} line(s) suggested" + (
        f"; {uncoded} uncoded (not in the chart)" if uncoded else ""
    )
    return (
        f"📄 Purchase invoice — {inv.vendor_name or '—'}, {inv.currency} "
        f"{inv.grand_total} · invoice {inv.invoice_number or '—'} · "
        f"{inv.invoice_date or '—'}\n"
        f"Totals: {totals}\n"
        f"GL coding: {coding}\n"
        f"Submitted for approval as {username}'s draft. {_NOT_FINAL}"
    )


def _record_photo_routed(
    persistent_audit_log, run, *, doc_type, routing, username, chat_id, media_type
):
    # Action name stays "telegram_bot.photo_routed" even for a document
    # upload — the audit trail is one continuous stream of "an upload got
    # routed to an agent" events, and media_type is what tells them apart.
    agent = "expense-agent" if doc_type == "expense" else "ap-agent"
    approval_request = getattr(run, "approval_request", None)
    persistent_audit_log.append(AuditEvent(
        timestamp=datetime.now(timezone.utc).isoformat(),
        agent="telegram_bot_prototype",
        action="telegram_bot.photo_routed",
        actor=username,
        inputs={"chat_id": chat_id, "doc_type": doc_type, "routing": routing, "agent": agent},
        output={
            "draft_ok": run.draft is not None,
            "approval_request_id": getattr(approval_request, "id", None),
            "media_type": media_type,
        },
    ))


def reject_document(
    persistent_audit_log, *, media_type: Optional[str], file_size: Optional[int],
    username: str, chat_id: int,
) -> Optional[str]:
    """Pre-download gate for a Telegram *document* message — decides purely
    from Telegram's own declared mime_type/file_size metadata, no bytes
    touched. Returns a friendly reply if the upload should be rejected (and
    records why on the persistent audit log); returns None if it's fine to
    download and hand to process_document.

    Caps mirror connectors.build_content_block's own per-type defaults
    exactly, so an upload that passes this gate also passes the agent's
    content-block build later — this check only exists to skip a pointless
    download, not to second-guess that cap.
    """
    if media_type not in _SUPPORTED_DOCUMENT_MEDIA_TYPES:
        reason = "unsupported_media_type"
        reply = (
            "I can only read PDF, PNG, or JPEG files here. That upload "
            f"({media_type or 'unknown type'}) isn't supported — resend it as "
            "one of those, or as a photo."
        )
    else:
        cap = (
            DEFAULT_MAX_PDF_BYTES if media_type == "application/pdf"
            else DEFAULT_MAX_IMAGE_BYTES
        )
        if file_size is not None and file_size > cap:
            reason = "content_too_large"
            reply = (
                f"That file is too large ({file_size / (1024 * 1024):.1f} MiB) — "
                f"the limit for a {media_type} upload is {cap // (1024 * 1024)} "
                "MiB. Try a smaller file."
            )
        else:
            return None

    persistent_audit_log.append(AuditEvent(
        timestamp=datetime.now(timezone.utc).isoformat(),
        agent="telegram_bot_prototype",
        action="telegram_bot.document_rejected",
        actor=username,
        inputs={"chat_id": chat_id, "media_type": media_type, "file_size": file_size},
        output={"reason": reason},
    ))
    return reply


# --------------------------------------------------------------------------
# orchestrator — sync; the bot handler calls this after an ack
# --------------------------------------------------------------------------
def process_photo(*, client, jpeg_bytes, caption, username, chat_id, persistent_audit_log) -> str:
    """Route → (classify) → run agent → record → summarize. Returns reply text.

    Never raises for an expected failure (bad photo, API error, unclear
    classification) — always returns a message.
    """
    doc_type = route_by_caption(caption)
    routing = "caption"

    if doc_type is None:
        try:
            doc_type = classify_photo(client, jpeg_bytes)
        except anthropic.AnthropicError as exc:
            return (
                f"The image service failed ({exc}). Your photo wasn't "
                "submitted — please try again shortly."
            )
        routing = "vision"
        if doc_type == "unclear":
            return (
                "I couldn't tell whether that's an expense receipt or a "
                "purchase/supplier invoice. Please resend the photo with a "
                "caption saying which — e.g. `expense receipt` or `supplier invoice`."
            )

    runner = run_expense if doc_type == "expense" else run_invoice
    try:
        run = runner(client, jpeg_bytes, username)
    except (anthropic.AnthropicError, ConnectorParseError, ValueError) as exc:
        return f"I couldn't process that photo ({exc}). Nothing was submitted."

    _record_photo_routed(
        persistent_audit_log, run,
        doc_type=doc_type, routing=routing, username=username, chat_id=chat_id,
        media_type="image/jpeg",
    )
    summarize = summarize_expense if doc_type == "expense" else summarize_invoice
    return summarize(run, username)


def process_document(
    *, client, content_bytes, media_type, caption, username, chat_id, persistent_audit_log
) -> str:
    """Route → (classify) → run agent → record → summarize, for a Telegram
    *document* upload. Same contract and shape as process_photo, generalized
    to any media type build_content_block supports (PDF included).

    Caller must have already: linked the chat (same as for photos — see
    telegram_bot_prototype.py's on_document), and called reject_document(...)
    before downloading. Never raises for an expected failure — always
    returns a message.
    """
    doc_type = route_by_caption(caption)
    routing = "caption"

    if doc_type is None:
        try:
            doc_type = classify_document(client, content_bytes, media_type)
        except (anthropic.AnthropicError, ContentTooLargeError, UnsupportedMediaTypeError) as exc:
            return (
                f"The document service failed ({exc}). Your file wasn't "
                "submitted — please try again shortly."
            )
        routing = "vision"
        if doc_type == "unclear":
            return (
                "I couldn't tell whether that's an expense receipt or a "
                "purchase/supplier invoice. Please resend with a caption "
                "saying which — e.g. `expense receipt` or `supplier invoice`."
            )

    runner = run_expense_document if doc_type == "expense" else run_invoice_document
    try:
        run = runner(client, content_bytes, media_type, username)
    except (anthropic.AnthropicError, ConnectorParseError, ValueError) as exc:
        return f"I couldn't process that file ({exc}). Nothing was submitted."

    _record_photo_routed(
        persistent_audit_log, run,
        doc_type=doc_type, routing=routing, username=username, chat_id=chat_id,
        media_type=media_type,
    )
    summarize = summarize_expense if doc_type == "expense" else summarize_invoice
    return summarize(run, username)
