"""
General-purpose Telegram bot client (python-telegram-bot).

Other scripts only need this module. Credentials can come from environment
variables or files under ./private — never hard-code a token.

    from telegram_client import TelegramBot

    bot = TelegramBot.autoload()          # env vars, then ./private
    bot.send("hello")                     # one-shot text
    bot.send_document("report.pdf")       # one-shot file
    await bot.send_async("hello")         # same, from async code

    bot.add_command("ping", on_ping)      # incoming commands
    bot.add_text_handler(on_text)
    bot.run_polling()                     # blocking long-poll loop

Credentials (first match wins for each):

    Token:   TELEGRAM_BOT_TOKEN  ->  private/bot_token  ->  private/token
             -> any other file in private/ that looks like a bot token
    Chat ID: TELEGRAM_CHAT_ID    ->  private/chat_id    ->  private/my_chat_id

Default incoming traffic is locked to the configured chat id(s). Pass
allow_all=True only if the bot must accept messages from anyone.

Docs: README.md, docs/api.md, docs/audit.md.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections.abc import Awaitable, Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

from telegram import Bot, Update
from telegram.constants import FileSizeLimit, MessageLimit, ParseMode
from telegram.error import (
    BadRequest,
    ChatMigrated,
    Conflict,
    Forbidden,
    InvalidToken,
    NetworkError,
    RetryAfter,
    TelegramError,
    TimedOut,
)
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.request import HTTPXRequest

__all__ = [
    "BASE_DIR",
    "CHAT_ID_ENV",
    "TOKEN_ENV",
    "TelegramBot",
    "TelegramConfigError",
    "TelegramPTB",
    "TokenRedactFilter",
    "configure_logging",
    "discover_credentials",
    "load_chat_id",
    "load_secret",
    "parse_chat_id",
    "parse_token",
    "split_message",
    "BadRequest",
    "ChatMigrated",
    "Conflict",
    "Forbidden",
    "InvalidToken",
    "NetworkError",
    "RetryAfter",
    "TelegramError",
    "TimedOut",
]

__version__ = "1.0.1"

BASE_DIR = Path(__file__).resolve().parent
TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
CHAT_ID_ENV = "TELEGRAM_CHAT_ID"

_TOKEN_FILENAMES = ("bot_token", "token", "telegram_token", "telegram_bot_token")
_CHAT_ID_FILENAMES = ("chat_id", "my_chat_id")
_TOKEN_RE = re.compile(r"^[0-9]{5,15}:[A-Za-z0-9_-]{30,}$")
_TOKEN_LEAK_RE = re.compile(r"[0-9]{5,15}:[A-Za-z0-9_-]{30,}")
_COMMAND_RE = re.compile(r"^[a-z0-9_]{1,32}$")
_HTTP_LOGGERS = ("httpx", "httpcore")
_PARSE_ERROR_HINTS = ("can't parse entities", "can't find end of the entity", "unsupported start tag")

_MAX_TEXT = int(MessageLimit.MAX_TEXT_LENGTH)
_MAX_CAPTION = int(MessageLimit.CAPTION_LENGTH)
_MAX_UPLOAD = int(FileSizeLimit.FILESIZE_UPLOAD)
_MAX_PHOTO = int(FileSizeLimit.PHOTOSIZE_UPLOAD)

HandlerCallback = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[Any]]

_LOG = logging.getLogger("telegram_client")


class TelegramConfigError(ValueError):
    """Invalid local configuration (token, chat id, missing files)."""


class TokenRedactFilter(logging.Filter):
    """Replace BotFather tokens in log records (httpx URLs, exceptions, args)."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self.redact(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {key: self.redact(value) for key, value in record.args.items()}
            else:
                record.args = tuple(self.redact(value) for value in record.args)
        return True

    @staticmethod
    def redact(value: Any) -> Any:
        if value is None or isinstance(value, (int, float, bool, bytes)):
            return value
        text = value if isinstance(value, str) else str(value)
        redacted = _TOKEN_LEAK_RE.sub("<TOKEN>", text)
        if redacted == text:
            return value
        return redacted


def configure_logging(*, level: int = logging.INFO, verbose: bool = False) -> None:
    """Console logging for example scripts.

    Default: library INFO, httpx/httpcore WARNING (no per-poll getUpdates lines).
    verbose=True: also log HTTP requests; tokens are still redacted.
    """
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    _install_token_redaction()
    http_level = logging.INFO if verbose else logging.WARNING
    for name in _HTTP_LOGGERS:
        logging.getLogger(name).setLevel(http_level)


def _install_token_redaction() -> None:
    """Attach TokenRedactFilter to HTTP loggers and existing root handlers."""
    targets = _HTTP_LOGGERS + ("telegram_client",)
    for name in targets:
        logger = logging.getLogger(name)
        if not any(isinstance(item, TokenRedactFilter) for item in logger.filters):
            logger.addFilter(TokenRedactFilter())
    root = logging.getLogger()
    for handler in root.handlers:
        if not any(isinstance(item, TokenRedactFilter) for item in handler.filters):
            handler.addFilter(TokenRedactFilter())


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def load_secret(path: str | Path) -> str:
    """Read a UTF-8 secret file. Strips BOM, whitespace, and wrapping quotes."""
    secret_path = Path(path)
    if not secret_path.is_file():
        raise TelegramConfigError(f"Secret file not found: {secret_path}")
    text = secret_path.read_text(encoding="utf-8-sig").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        text = text[1:-1].strip()
    if not text:
        raise TelegramConfigError(f"Secret file is empty: {secret_path}")
    return text


def parse_token(value: str) -> str:
    """Validate a Bot API token. Never log the return value."""
    token = (value or "").strip()
    if token.startswith("bot"):
        token = token[3:]
    if "api.telegram.org" in token:
        raise TelegramConfigError(
            "Token looks like an API URL. Use only the bot token from BotFather "
            "(digits, colon, then the secret)."
        )
    if not _TOKEN_RE.fullmatch(token):
        raise TelegramConfigError(
            "Invalid Telegram bot token. Expected '<bot_id>:<secret>' from BotFather."
        )
    return token


def parse_chat_id(value: str | int) -> int | str:
    """Normalize a chat id to int, or keep @channelusername as a string."""
    if isinstance(value, bool):
        raise TelegramConfigError(f"Invalid chat id: {value!r}")
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        raise TelegramConfigError("Chat id is empty")
    if text.startswith("@") and len(text) > 1 and " " not in text:
        return text
    if text.lstrip("-").isdigit():
        return int(text)
    raise TelegramConfigError(
        f"Invalid chat id {value!r}. Use a numeric id (groups are negative) "
        "or @channelusername."
    )


def split_message(text: str, limit: int = _MAX_TEXT) -> list[str]:
    """Split text into Telegram-sized chunks, preferring newline then space."""
    if limit <= 0:
        raise ValueError("limit must be positive")
    remaining = text
    chunks: list[str] = []
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        window = remaining[:limit]
        cut = window.rfind("\n")
        if cut < limit // 4:
            cut = window.rfind(" ")
        if cut < limit // 4:
            cut = limit
        piece = remaining[:cut].rstrip()
        if not piece:
            piece = remaining[:limit]
            cut = limit
        chunks.append(piece)
        remaining = remaining[cut:].lstrip()
    return chunks


def discover_credentials(
    private_dir: str | Path | None = None,
    *,
    token_file: str | Path | None = None,
    chat_id_file: str | Path | None = None,
) -> tuple[str | None, int | str | None]:
    """
    Find a token and chat id on disk.

    Returns (token_or_none, chat_id_or_none). Missing files are not an error;
    TelegramBot.autoload() raises if a token still cannot be resolved.
    """
    directory = Path(private_dir) if private_dir else BASE_DIR / "private"
    token: str | None = None
    chat_id: int | str | None = None

    if token_file is not None:
        token = parse_token(load_secret(token_file))
    if chat_id_file is not None:
        chat_id = parse_chat_id(load_secret(chat_id_file))

    if not directory.is_dir():
        return token, chat_id

    files = {
        path.name: path
        for path in directory.iterdir()
        if path.is_file() and not path.name.startswith(".")
    }

    if token is None:
        for name in _TOKEN_FILENAMES:
            if name in files:
                token = parse_token(load_secret(files[name]))
                break

    if chat_id is None:
        for name in _CHAT_ID_FILENAMES:
            if name in files:
                chat_id = parse_chat_id(load_secret(files[name]))
                break

    if token is None:
        skip = set(_TOKEN_FILENAMES) | set(_CHAT_ID_FILENAMES)
        matches: list[str] = []
        for name, path in files.items():
            if name in skip:
                continue
            try:
                matches.append(parse_token(load_secret(path)))
            except TelegramConfigError:
                continue
        if len(matches) == 1:
            token = matches[0]
        elif len(matches) > 1:
            raise TelegramConfigError(
                f"Multiple token-like files in {directory}. "
                f"Rename the real token to one of: {', '.join(_TOKEN_FILENAMES)}"
            )

    return token, chat_id


def load_chat_id(path: str | Path | None = None) -> int:
    """Load a numeric chat id from a file (default: private/my_chat_id)."""
    chat_path = Path(path) if path else BASE_DIR / "private" / "my_chat_id"
    if not chat_path.is_file():
        fallback = BASE_DIR / "private" / "chat_id"
        if path is None and fallback.is_file():
            chat_path = fallback
    parsed = parse_chat_id(load_secret(chat_path))
    if isinstance(parsed, str):
        raise TelegramConfigError(f"Expected a numeric chat id in {chat_path}")
    return parsed


def _is_parse_error(exc: BadRequest) -> bool:
    message = str(exc).lower()
    return any(hint in message for hint in _PARSE_ERROR_HINTS)


def _retry_after_seconds(exc: RetryAfter) -> float:
    value = exc.retry_after
    total_seconds = getattr(value, "total_seconds", None)
    if callable(total_seconds):
        return float(total_seconds())
    return float(value)


def _validate_local_file(path: str | Path, *, max_bytes: int, kind: str) -> Path:
    file_path = Path(path)
    if not file_path.is_file():
        raise TelegramConfigError(f"{kind.capitalize()} not found: {file_path}")
    size = file_path.stat().st_size
    if size <= 0:
        raise TelegramConfigError(f"{kind.capitalize()} is empty: {file_path}")
    if size > max_bytes:
        raise TelegramConfigError(
            f"{kind.capitalize()} is {size} bytes; Telegram limit is {max_bytes} bytes: {file_path}"
        )
    return file_path


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class TelegramBot:
    """Send messages and optionally long-poll for incoming commands."""

    def __init__(
        self,
        token: str | None = None,
        chat_id: str | int | None = None,
        *,
        allowed_chat_ids: Iterable[str | int] | None = None,
        allow_all: bool = False,
        parse_mode: str | None = None,
        plain_fallback: bool = True,
        max_retries: int = 3,
        retry_base: float = 1.0,
        retry_cap: float = 30.0,
        max_retry_after: float = 120.0,
        connect_timeout: float = 10.0,
        read_timeout: float = 30.0,
        write_timeout: float = 30.0,
        pool_timeout: float = 5.0,
        media_write_timeout: float = 120.0,
        get_updates_read_timeout: float = 40.0,
        token_file: str | Path | None = None,
        chat_id_file: str | Path | None = None,
        private_dir: str | Path | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        if token is None or chat_id is None:
            discovered_token, discovered_chat = discover_credentials(
                private_dir, token_file=token_file, chat_id_file=chat_id_file
            )
            token = token or discovered_token
            if chat_id is None:
                chat_id = discovered_chat

        if not token:
            raise TelegramConfigError(
                "No bot token. Set TELEGRAM_BOT_TOKEN, pass token=..., or put the "
                f"token in {BASE_DIR / 'private' / 'bot_token'}."
            )

        self.token = parse_token(token)
        self.chat_id: int | str | None = parse_chat_id(chat_id) if chat_id is not None else None
        self.allow_all = bool(allow_all)
        self.parse_mode = self._normalize_parse_mode(parse_mode)
        self.plain_fallback = bool(plain_fallback)
        self.max_retries = max(0, int(max_retries))
        self.retry_base = float(retry_base)
        self.retry_cap = float(retry_cap)
        self.max_retry_after = float(max_retry_after)
        self.connect_timeout = float(connect_timeout)
        self.read_timeout = float(read_timeout)
        self.write_timeout = float(write_timeout)
        self.pool_timeout = float(pool_timeout)
        self.media_write_timeout = float(media_write_timeout)
        self.get_updates_read_timeout = float(get_updates_read_timeout)
        self._log = logger or _LOG
        _install_token_redaction()

        allowed: set[int | str] = set()
        if self.chat_id is not None:
            allowed.add(self.chat_id)
        if allowed_chat_ids is not None:
            allowed.update(parse_chat_id(item) for item in allowed_chat_ids)
        self.allowed_chat_ids = allowed

        self._commands: list[tuple[str, HandlerCallback]] = []
        self._text_handler: HandlerCallback | None = None
        self._extra_handlers: list[Any] = []
        self._startup_message: str | None = None
        self._app: Application | None = None

    def __repr__(self) -> str:
        return (
            f"TelegramBot(chat_id={self.chat_id!r}, "
            f"allow_all={self.allow_all}, "
            f"commands={len(self._commands)})"
        )

    @classmethod
    def autoload(cls, **kwargs: Any) -> TelegramBot:
        """Build a client from environment variables, then ./private files."""
        env_token = os.environ.get(TOKEN_ENV, "").strip() or None
        env_chat = os.environ.get(CHAT_ID_ENV, "").strip() or None
        kwargs = dict(kwargs)
        token = kwargs.pop("token", None) or env_token
        chat_id = kwargs.pop("chat_id", None)
        if chat_id is None and env_chat:
            chat_id = env_chat
        return cls(token=token, chat_id=chat_id, **kwargs)

    # --- public send API ---------------------------------------------------

    def send(
        self,
        text: str,
        *,
        chat_id: str | int | None = None,
        parse_mode: str | None = ...,  # type: ignore[assignment]
        disable_web_page_preview: bool | None = None,
        disable_notification: bool | None = None,
        **kwargs: Any,
    ) -> list[Any]:
        """Send text (sync). Long messages are split. Returns one Message per chunk."""
        return self._run(
            self.send_async(
                text,
                chat_id=chat_id,
                parse_mode=parse_mode,
                disable_web_page_preview=disable_web_page_preview,
                disable_notification=disable_notification,
                **kwargs,
            )
        )

    async def send_async(
        self,
        text: str,
        *,
        chat_id: str | int | None = None,
        parse_mode: str | None = ...,  # type: ignore[assignment]
        disable_web_page_preview: bool | None = None,
        disable_notification: bool | None = None,
        **kwargs: Any,
    ) -> list[Any]:
        """Send text (async). Long messages are split. Returns one Message per chunk."""
        target = self._require_chat_id(chat_id)
        body = "" if text is None else str(text)
        if not body.strip():
            raise TelegramConfigError("Cannot send an empty message")
        mode = self.parse_mode if parse_mode is ... else self._normalize_parse_mode(parse_mode)
        chunks = split_message(body, _MAX_TEXT)
        sent: list[Any] = []
        async with self._open_bot() as bot:
            for chunk in chunks:
                sent.append(
                    await self._invoke(
                        bot.send_message,
                        chat_id=target,
                        text=chunk,
                        parse_mode=mode,
                        disable_web_page_preview=disable_web_page_preview,
                        disable_notification=disable_notification,
                        **kwargs,
                    )
                )
        return sent

    send_message = send
    send_message_async = send_async

    def send_document(
        self,
        document_path: str | Path,
        caption: str = "",
        *,
        chat_id: str | int | None = None,
        parse_mode: str | None = ...,  # type: ignore[assignment]
        disable_notification: bool | None = None,
        **kwargs: Any,
    ) -> Any:
        """Upload a local file as a document (sync)."""
        return self._run(
            self.send_document_async(
                document_path,
                caption,
                chat_id=chat_id,
                parse_mode=parse_mode,
                disable_notification=disable_notification,
                **kwargs,
            )
        )

    async def send_document_async(
        self,
        document_path: str | Path,
        caption: str = "",
        *,
        chat_id: str | int | None = None,
        parse_mode: str | None = ...,  # type: ignore[assignment]
        disable_notification: bool | None = None,
        **kwargs: Any,
    ) -> Any:
        """Upload a local file as a document (async)."""
        target = self._require_chat_id(chat_id)
        path = _validate_local_file(document_path, max_bytes=_MAX_UPLOAD, kind="document")
        caption_text = self._prepare_caption(caption)
        mode = self.parse_mode if parse_mode is ... else self._normalize_parse_mode(parse_mode)
        async with self._open_bot() as bot:
            # Pass a Path so a retry re-opens the file instead of reusing an
            # already-consumed binary handle.
            return await self._invoke(
                bot.send_document,
                chat_id=target,
                document=path,
                filename=path.name,
                caption=caption_text,
                parse_mode=mode,
                disable_notification=disable_notification,
                write_timeout=self.media_write_timeout,
                **kwargs,
            )

    def send_photo(
        self,
        photo_path: str | Path,
        caption: str = "",
        *,
        chat_id: str | int | None = None,
        parse_mode: str | None = ...,  # type: ignore[assignment]
        disable_notification: bool | None = None,
        **kwargs: Any,
    ) -> Any:
        """Upload a local photo (sync)."""
        return self._run(
            self.send_photo_async(
                photo_path,
                caption,
                chat_id=chat_id,
                parse_mode=parse_mode,
                disable_notification=disable_notification,
                **kwargs,
            )
        )

    async def send_photo_async(
        self,
        photo_path: str | Path,
        caption: str = "",
        *,
        chat_id: str | int | None = None,
        parse_mode: str | None = ...,  # type: ignore[assignment]
        disable_notification: bool | None = None,
        **kwargs: Any,
    ) -> Any:
        """Upload a local photo (async)."""
        target = self._require_chat_id(chat_id)
        path = _validate_local_file(photo_path, max_bytes=_MAX_PHOTO, kind="photo")
        caption_text = self._prepare_caption(caption)
        mode = self.parse_mode if parse_mode is ... else self._normalize_parse_mode(parse_mode)
        async with self._open_bot() as bot:
            return await self._invoke(
                bot.send_photo,
                chat_id=target,
                photo=path,
                filename=path.name,
                caption=caption_text,
                parse_mode=mode,
                disable_notification=disable_notification,
                write_timeout=self.media_write_timeout,
                **kwargs,
            )

    def get_me(self) -> Any:
        """Call getMe (sync). Useful as a token / connectivity check."""
        return self._run(self.get_me_async())

    async def get_me_async(self) -> Any:
        """Call getMe (async)."""
        async with self._open_bot() as bot:
            return await self._invoke(bot.get_me)

    # --- incoming handlers -------------------------------------------------

    def add_command(self, name: str, callback: HandlerCallback) -> TelegramBot:
        """Register an async command handler, e.g. add_command('ping', on_ping)."""
        command = name.lstrip("/").strip().lower()
        if not _COMMAND_RE.fullmatch(command):
            raise TelegramConfigError(
                f"Invalid command name {name!r}. Use 1-32 chars: a-z, 0-9, underscore."
            )
        if not callable(callback):
            raise TelegramConfigError("Command callback must be a callable")
        self._commands.append((command, callback))
        return self

    def add_text_handler(self, callback: HandlerCallback) -> TelegramBot:
        """Register an async handler for non-command text messages."""
        if not callable(callback):
            raise TelegramConfigError("Text handler must be a callable")
        self._text_handler = callback
        return self

    def add_handler(self, handler: Any) -> TelegramBot:
        """Register a raw python-telegram-bot handler (escape hatch)."""
        self._extra_handlers.append(handler)
        return self

    def run_polling(
        self,
        *,
        startup_message: str | None = None,
        drop_pending_updates: bool = True,
        poll_timeout: int = 20,
        allowed_updates: Sequence[str] | None = None,
        **kwargs: Any,
    ) -> None:
        """Build the Application and block on long polling until stopped."""
        if not self.allow_all and not self.allowed_chat_ids:
            raise TelegramConfigError(
                "Refusing to poll with no chat allowlist. Pass chat_id, "
                "allowed_chat_ids, or allow_all=True."
            )
        self._startup_message = startup_message
        application = self._build_application()
        self._app = application
        self._log.info("Starting polling (drop_pending_updates=%s)", drop_pending_updates)
        application.run_polling(
            drop_pending_updates=drop_pending_updates,
            timeout=poll_timeout,
            bootstrap_retries=-1,
            allowed_updates=list(allowed_updates) if allowed_updates is not None else None,
            **kwargs,
        )

    listen_and_reply = run_polling

    # --- internals ---------------------------------------------------------

    def _open_bot(self) -> Bot:
        request = HTTPXRequest(
            connection_pool_size=4,
            connect_timeout=self.connect_timeout,
            read_timeout=self.read_timeout,
            write_timeout=self.write_timeout,
            pool_timeout=self.pool_timeout,
            media_write_timeout=self.media_write_timeout,
        )
        return Bot(self.token, request=request)

    def _require_chat_id(self, chat_id: str | int | None) -> int | str:
        target = self.chat_id if chat_id is None else parse_chat_id(chat_id)
        if target is None:
            raise TelegramConfigError(
                "No chat id. Pass chat_id=... to send(), set TELEGRAM_CHAT_ID, "
                f"or put it in {BASE_DIR / 'private' / 'chat_id'}."
            )
        return target

    def _prepare_caption(self, caption: str) -> str | None:
        if caption is None:
            return None
        text = str(caption)
        if not text:
            return None
        if len(text) > _MAX_CAPTION:
            raise TelegramConfigError(
                f"Caption is {len(text)} characters; Telegram limit is {_MAX_CAPTION}."
            )
        return text

    @staticmethod
    def _normalize_parse_mode(parse_mode: str | None) -> str | None:
        if parse_mode is None:
            return None
        raw = str(parse_mode).strip()
        if not raw:
            return None
        lookup = {
            "markdown": ParseMode.MARKDOWN,
            "markdownv2": ParseMode.MARKDOWN_V2,
            "markdown_v2": ParseMode.MARKDOWN_V2,
            "html": ParseMode.HTML,
        }
        key = raw.replace("-", "").replace(" ", "").lower()
        if key not in lookup:
            raise TelegramConfigError(
                f"Unknown parse_mode {parse_mode!r}. Use None, 'HTML', 'Markdown', or 'MarkdownV2'."
            )
        return lookup[key]

    def _redact(self, text: Any) -> str:
        message = "" if text is None else str(text)
        if self.token:
            message = message.replace(self.token, "<TOKEN>")
        return message

    def _run(self, coro: Awaitable[Any]) -> Any:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        close = getattr(coro, "close", None)
        if callable(close):
            close()
        raise RuntimeError(
            "An event loop is already running. Use the async method "
            "(send_async / send_document_async / send_photo_async) instead."
        )

    async def _invoke(self, func: Callable[..., Awaitable[Any]], *args: Any, **kwargs: Any) -> Any:
        """Call a Bot API coroutine with retries for flood-wait and network errors."""
        attempt = 0
        last_error: BaseException | None = None
        call_kwargs = dict(kwargs)

        while attempt <= self.max_retries:
            try:
                return await func(*args, **call_kwargs)
            except RetryAfter as exc:
                last_error = exc
                delay = _retry_after_seconds(exc) + 0.5
                if delay > self.max_retry_after:
                    raise
                self._log.warning("Rate limited; retrying in %.1fs", delay)
                await asyncio.sleep(delay)
                attempt += 1
            except ChatMigrated as exc:
                last_error = exc
                new_id = int(exc.new_chat_id)
                old = call_kwargs.get("chat_id")
                self._log.warning("Chat migrated %s -> %s", old, new_id)
                if old is not None and self.chat_id is not None and parse_chat_id(old) == self.chat_id:
                    self.allowed_chat_ids.discard(self.chat_id)
                    self.chat_id = new_id
                    self.allowed_chat_ids.add(new_id)
                call_kwargs["chat_id"] = new_id
                attempt += 1
            except BadRequest as exc:
                last_error = exc
                if (
                    self.plain_fallback
                    and call_kwargs.get("parse_mode")
                    and _is_parse_error(exc)
                ):
                    self._log.warning(
                        "Parse mode failed (%s); retrying as plain text",
                        self._redact(exc),
                    )
                    call_kwargs["parse_mode"] = None
                    continue
                raise
            except (InvalidToken, Forbidden, Conflict):
                raise
            except (TimedOut, NetworkError) as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                delay = min(self.retry_base * (2 ** attempt), self.retry_cap)
                self._log.warning(
                    "Network error (%s); retrying in %.1fs",
                    self._redact(exc),
                    delay,
                )
                await asyncio.sleep(delay)
                attempt += 1
            except TelegramError:
                raise
        assert last_error is not None
        raise last_error

    def _chat_filter(self) -> Any | None:
        if self.allow_all:
            return None
        int_ids = [item for item in self.allowed_chat_ids if isinstance(item, int)]
        usernames = [
            item[1:] if str(item).startswith("@") else str(item)
            for item in self.allowed_chat_ids
            if isinstance(item, str)
        ]
        chat_filter = None
        if int_ids:
            chat_filter = filters.Chat(chat_id=int_ids)
        if usernames:
            name_filter = filters.Chat(username=usernames)
            chat_filter = name_filter if chat_filter is None else chat_filter | name_filter
        return chat_filter

    def _wrap(self, callback: HandlerCallback) -> HandlerCallback:
        async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Any:
            if update.effective_message is None:
                return None
            return await callback(update, context)

        return wrapped

    async def _on_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        self._log.exception(
            "Handler or polling error: %s",
            self._redact(getattr(context, "error", None)),
        )

    async def _on_startup(self, application: Application) -> None:
        if not self._startup_message:
            return
        if self.chat_id is None:
            self._log.warning("Startup message skipped: no default chat id")
            return
        try:
            await self._invoke(
                application.bot.send_message,
                chat_id=self.chat_id,
                text=self._startup_message,
            )
        except Exception as exc:
            self._log.warning("Startup notification failed: %s", self._redact(exc))

    async def _on_stop(self, application: Application) -> None:
        self._log.info("Polling stopped")

    async def _log_unauthorized(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat = update.effective_chat
        user = update.effective_user
        self._log.warning(
            "Ignored update from unauthorized chat_id=%s user_id=%s",
            getattr(chat, "id", None),
            getattr(user, "id", None),
        )

    def _build_rate_limiter(self) -> Any | None:
        try:
            from telegram.ext import AIORateLimiter
        except (ImportError, RuntimeError):
            return None
        return AIORateLimiter(max_retries=self.max_retries)

    def _build_application(self) -> Application:
        builder = (
            Application.builder()
            .token(self.token)
            .connect_timeout(self.connect_timeout)
            .read_timeout(self.read_timeout)
            .write_timeout(self.write_timeout)
            .pool_timeout(self.pool_timeout)
            .media_write_timeout(self.media_write_timeout)
            .get_updates_read_timeout(self.get_updates_read_timeout)
            .post_init(self._on_startup)
            .post_stop(self._on_stop)
        )
        limiter = self._build_rate_limiter()
        if limiter is not None:
            builder = builder.rate_limiter(limiter)
        application = builder.build()
        application.add_error_handler(self._on_error)

        chat_filter = self._chat_filter()
        for name, callback in self._commands:
            if chat_filter is None:
                application.add_handler(CommandHandler(name, self._wrap(callback)))
            else:
                application.add_handler(
                    CommandHandler(name, self._wrap(callback), filters=chat_filter)
                )

        if self._text_handler is not None:
            text_filter = filters.TEXT & ~filters.COMMAND
            if chat_filter is not None:
                text_filter = chat_filter & text_filter
            application.add_handler(MessageHandler(text_filter, self._wrap(self._text_handler)))

        for handler in self._extra_handlers:
            application.add_handler(handler)

        if chat_filter is not None:
            application.add_handler(
                MessageHandler(~chat_filter, self._log_unauthorized),
                group=1,
            )
        return application


TelegramPTB = TelegramBot
