"""LOCAL-ONLY Telegram bot prototype — step 2 of a staged build.

    ┌───────────────────────────────────────────────────────────────┐
    │  LOCAL EXPLORATION ONLY. Long-polling, no public URL.          │
    │  Not linked from app.py. Not deployed. Fictional users only.   │
    └───────────────────────────────────────────────────────────────┘

Proves real Telegram connectivity + real identity resolution via
platform/telegram-link, and routes a LINKED user's photo to the right agent.

On a text message:
  - linked chat         -> "Hi {username}, message received — I'm not
                            processing content yet, just confirming who you are."
  - unlinked + a code    -> redeem it; friendly success / failure reply
  - unlinked + anything  -> instructions on how to link

On a photo from a LINKED user (see telegram_bot_photo.py):
  - caption keywords ("expense"/"receipt" vs "invoice"/"purchase"/"bill")
    route directly; otherwise one Claude vision call classifies it, and an
    "unclear" result asks the user to resend with a caption
  - the routed agent (expense-agent / ap-agent) drafts and submits through
    platform/approvals with the linked user as preparer
  - stops at drafted + submitted — no Zoho, no ERP write
  - without ANTHROPIC_API_KEY the photo is acknowledged with a clear message,
    not processed, not crashed

Tokens/keys come from the environment (or KEY=VALUE lines in ./.env) —
TELEGRAM_BOT_TOKEN and ANTHROPIC_API_KEY, never hardcoded.

    pip install -r requirements-telegram.txt
    export TELEGRAM_BOT_TOKEN=...             # from @BotFather, or put in ./.env
    export ANTHROPIC_API_KEY=...              # optional; needed for photo processing

    python telegram_bot_prototype.py gencode dana.acme   # mint a linking code
    python telegram_bot_prototype.py run                 # start the bot (default)

The gencode subcommand is the operator's stand-in for the future "Link
Telegram" button in the web app. State (auth users, links, audit log) lives
in ./telegram_bot_prototype_data/ (gitignored) and persists across restarts.
"""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PLATFORM = ROOT / "platform"

for _p in (PLATFORM / "telegram-link", PLATFORM / "auth", PLATFORM / "audit-log"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from audit_log import AuditLogStore  # noqa: E402
from auth import AuthStore  # noqa: E402
from telegram_link import (  # noqa: E402
    ChatAlreadyLinked,
    InvalidLinkCode,
    LinkCodeAlreadyUsed,
    LinkCodeExpired,
    TelegramLinkError,
    TelegramLinkStore,
    looks_like_code,
)

DATA_DIR = ROOT / "telegram_bot_prototype_data"
_TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
_ANTHROPIC_KEY_ENV = "ANTHROPIC_API_KEY"


def anthropic_key():
    """The Claude API key from the environment (or ./.env), or None. Same
    handling as the bot token — never hardcoded."""
    key = os.environ.get(_ANTHROPIC_KEY_ENV)
    return key.strip() if key and key.strip() else None

_INSTRUCTIONS = (
    "You're not linked yet. To connect this Telegram chat to your account, "
    "get a one-time linking code and send it to me here.\n\n"
    "It's a 12-character code that looks like `xK3p_2Qz9Ab-`. Codes expire "
    "after about 10 minutes."
)


# --------------------------------------------------------------------------
# .env loader — no dependency (repo has no python-dotenv)
# --------------------------------------------------------------------------
def load_dotenv(path: Path = None) -> None:
    path = path or (ROOT / ".env")
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


# --------------------------------------------------------------------------
# stores (persistent, gitignored) + first-run fictional-user seed
# --------------------------------------------------------------------------
def _seed_fictional_users(auth_store: AuthStore) -> None:
    if auth_store.get_user("dana.acme") is not None:
        return
    import importlib.util

    path = PLATFORM / "telegram-link" / "tests" / "fixtures.py"
    spec = importlib.util.spec_from_file_location("_tgbot_fixtures", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for username, (password, role) in mod.FICTIONAL_USERS.items():
        auth_store.create_user(username, password, role)


class Stores:
    """auth + audit-log + telegram-link, all backed by files in DATA_DIR.

    SQLite note: python-telegram-bot runs one asyncio loop on the calling
    thread, and these connections are opened on that same thread before
    run_polling() — so the sync DB calls inside async handlers are
    single-threaded and safe. A higher-volume bot would move them off the
    loop; out of scope here.
    """

    def __init__(self) -> None:
        DATA_DIR.mkdir(exist_ok=True)
        self.auth = AuthStore(DATA_DIR / "auth.db")
        self.audit_log = AuditLogStore(DATA_DIR / "audit.db")
        _seed_fictional_users(self.auth)
        self.links = TelegramLinkStore(
            DATA_DIR / "link.db", audit_log=self.audit_log, auth_store=self.auth
        )

    def close(self) -> None:
        self.links.close()
        self.audit_log.close()
        self.auth.close()


# --------------------------------------------------------------------------
# routing — pure, testable, no `telegram` import
# --------------------------------------------------------------------------
def route_message(links: TelegramLinkStore, chat_id: int, text: str) -> str:
    """The reply for one inbound text message.

    Side effects are only what telegram-link itself does: a redeem attempt
    (success or failure) writes to the injected audit log.
    """
    user = links.resolve_chat_id(chat_id)
    if user is not None:
        return (
            f"Hi {user.username}, message received — I'm not processing "
            "content yet, just confirming who you are."
        )
    if looks_like_code(text):
        return redeem_reply(links, chat_id, text)
    return _INSTRUCTIONS


def redeem_reply(links: TelegramLinkStore, chat_id: int, code: str) -> str:
    try:
        link = links.redeem_link_code(code, chat_id)
    except LinkCodeAlreadyUsed:
        return "That code has already been used. Ask for a fresh one."
    except LinkCodeExpired:
        return "That code has expired (they last about 10 minutes). Ask for a fresh one."
    except InvalidLinkCode:
        return (
            "That doesn't match any active linking code. Double-check it, or "
            "request a new one."
        )
    except ChatAlreadyLinked:
        return (
            "This chat is already linked to another account. An admin needs to "
            "revoke that link before it can be re-linked."
        )
    except TelegramLinkError:
        return "Something went wrong linking that code. Please try again."
    return (
        f"✅ Linked — you're connected as {link.username}. I still "
        "don't read message content yet; this just confirms who you are."
    )


# --------------------------------------------------------------------------
# subcommands
# --------------------------------------------------------------------------
def cmd_gencode(username: str, ttl_seconds: int) -> int:
    stores = Stores()
    try:
        code = stores.links.generate_link_code(username, ttl_seconds=ttl_seconds)
    except TelegramLinkError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        stores.close()
    print(code)
    print(f"(valid ~{max(ttl_seconds // 60, 0)} min — send it to the bot as a message)",
          file=sys.stderr)
    return 0


def _console_log(chat_id: int, kind: str, reply: str) -> None:
    print(f"  chat={chat_id} {kind:<10} -> {reply.splitlines()[0]}")


def cmd_run() -> int:
    token = os.environ.get(_TOKEN_ENV)
    if not token:
        print(
            f"error: {_TOKEN_ENV} is not set.\n"
            f"  export {_TOKEN_ENV}=...  (from @BotFather), or add a line\n"
            f"  {_TOKEN_ENV}=...  to ./.env",
            file=sys.stderr,
        )
        return 1

    from telegram import Update
    from telegram.ext import (
        Application,
        CommandHandler,
        ContextTypes,
        MessageHandler,
        filters,
    )

    import telegram_bot_photo as photo  # heavy (agents + anthropic) — load only when running

    stores = Stores()

    async def on_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        arg = context.args[0] if context.args else ""
        if arg and looks_like_code(arg):
            reply = redeem_reply(stores.links, chat_id, arg)
        else:
            user = stores.links.resolve_chat_id(chat_id)
            reply = (
                f"Hi {user.username} — you're already linked. (I don't "
                "process message content yet.)"
                if user is not None
                else _INSTRUCTIONS
            )
        _console_log(chat_id, "/start", reply)
        await update.message.reply_text(reply)

    async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        reply = route_message(stores.links, chat_id, update.message.text or "")
        _console_log(chat_id, "text", reply)
        await update.message.reply_text(reply)

    async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        user = stores.links.resolve_chat_id(chat_id)
        if user is None:
            _console_log(chat_id, "photo", "not linked -> instructions")
            await update.message.reply_text(_INSTRUCTIONS)
            return

        key = anthropic_key()
        if not key:
            reply = (
                f"📷 Got your photo, {user.username}. I can't read images yet — "
                f"{_ANTHROPIC_KEY_ENV} isn't configured. Your photo wasn't lost; "
                "resend once it's set up."
            )
            _console_log(chat_id, "photo", "no api key")
            await update.message.reply_text(reply)
            return

        await update.message.reply_text(f"📄 Got it, {user.username} — reading the photo…")
        tg_file = await context.bot.get_file(update.message.photo[-1].file_id)
        jpeg = bytes(await tg_file.download_as_bytearray())

        import anthropic

        # Synchronous: this blocks the event loop for ~15-30s (vision extract +
        # retrieval + drafting). Acceptable for a local single-user prototype;
        # the ack above keeps the UX honest.
        reply = photo.process_photo(
            client=anthropic.Anthropic(api_key=key),
            jpeg_bytes=jpeg,
            caption=update.message.caption,
            username=user.username,
            chat_id=chat_id,
            persistent_audit_log=stores.audit_log,
        )
        _console_log(chat_id, "photo", reply)
        await update.message.reply_text(reply)

    async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        user = stores.links.resolve_chat_id(chat_id)
        who = f"Hi {user.username}, " if user is not None else ""
        reply = (
            f"{who}send that as a *photo* rather than a file and I'll try to "
            "read it. (Attachment/document handling isn't built yet.)"
        )
        _console_log(chat_id, "document", reply)
        await update.message.reply_text(reply)

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", on_start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, on_document))

    print(f"telegram_bot_prototype: polling. state in {DATA_DIR}/  (Ctrl-C to stop)",
          flush=True)
    try:
        app.run_polling()
    finally:
        stores.close()
    return 0


def main(argv=None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Local-only Telegram bot prototype.")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("run", help="start the bot (long-polling) — the default")
    g = sub.add_parser("gencode", help="mint a one-time linking code for a user")
    g.add_argument("username")
    g.add_argument("--ttl", type=int, default=600, help="lifetime in seconds (default 600)")
    args = parser.parse_args(argv)

    if args.cmd == "gencode":
        return cmd_gencode(args.username, args.ttl)
    return cmd_run()


if __name__ == "__main__":
    sys.exit(main())
