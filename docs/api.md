# API reference

Public surface of `telegram_client.py` (version 1.0.1). Internals whose names
start with `_` are not part of the contract.

```python
from telegram_client import TelegramBot
```

`TelegramPTB` is an alias of `TelegramBot` (old name).

## Construction

### `TelegramBot.autoload(**kwargs) -> TelegramBot`

Preferred entry point. Reads `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`, then
falls back to `private/`. Extra keyword arguments go to `__init__`.

### `TelegramBot(token=None, chat_id=None, **options)`

If `token` or `chat_id` is omitted, the missing piece is loaded from disk
(`private_dir`, `token_file`, `chat_id_file`).

| Option | Default | Meaning |
| --- | --- | --- |
| `allowed_chat_ids` | `{chat_id}` if set | Extra chats allowed to talk to the bot |
| `allow_all` | `False` | Accept incoming updates from any chat |
| `parse_mode` | `None` (plain text) | `"HTML"`, `"Markdown"`, `"MarkdownV2"` |
| `plain_fallback` | `True` | Retry as plain text if entity parsing fails |
| `max_retries` | `3` | Retries after the first attempt |
| `retry_base` | `1.0` | Initial backoff seconds (doubles each try) |
| `retry_cap` | `30.0` | Max backoff for network errors |
| `max_retry_after` | `120.0` | Give up if Telegram flood-wait exceeds this |
| `connect_timeout` | `10.0` | HTTP connect timeout (seconds) |
| `read_timeout` | `30.0` | HTTP read timeout |
| `write_timeout` | `30.0` | HTTP write timeout |
| `pool_timeout` | `5.0` | HTTP connection-pool wait |
| `media_write_timeout` | `120.0` | Upload write timeout |
| `get_updates_read_timeout` | `40.0` | Long-poll HTTP read (must exceed poll timeout) |
| `token_file` | discovered | Explicit token path |
| `chat_id_file` | discovered | Explicit chat-id path |
| `private_dir` | `<lib>/private` | Directory to scan for secrets |
| `logger` | `logging.getLogger("telegram_client")` | Override logger |

Instance attributes callers may read: `chat_id`, `allowed_chat_ids`,
`allow_all`, `parse_mode`. Do not log `token`.

## Helpers

| Function | Returns | Notes |
| --- | --- | --- |
| `parse_token(value)` | `str` | Validates BotFather form `digits:secret` |
| `parse_chat_id(value)` | `int` or `@username` | Rejects bools and empty strings |
| `load_secret(path)` | `str` | UTF-8, strips BOM / wrapping quotes |
| `load_chat_id(path=None)` | `int` | Default `private/my_chat_id` then `private/chat_id` |
| `discover_credentials(private_dir=None, *, token_file=None, chat_id_file=None)` | `(token or None, chat_id or None)` | Missing files are not an error |
| `split_message(text, limit=4096)` | `list[str]` | Prefers newline, then space |
| `configure_logging(*, level=INFO, verbose=False)` | `None` | Example-script logging: quiet httpx unless `verbose` |
| `TokenRedactFilter` | `logging.Filter` | Replaces BotFather tokens in log records |

Env names: `TOKEN_ENV` (`TELEGRAM_BOT_TOKEN`), `CHAT_ID_ENV` (`TELEGRAM_CHAT_ID`).
`BASE_DIR` is the directory that contains `telegram_client.py`.

## Send

Sync methods raise `RuntimeError` if an event loop is already running — use the
`*_async` twin instead.

| Sync | Async | Returns |
| --- | --- | --- |
| `send(text, *, chat_id=None, parse_mode=..., ...)` | `send_async` | `list[Message]` (one per 4096-char chunk) |
| `send_message` | `send_message_async` | aliases of `send` |
| `send_document(path, caption="", *, chat_id=None, ...)` | `send_document_async` | `Message` |
| `send_photo(path, caption="", *, chat_id=None, ...)` | `send_photo_async` | `Message` |
| `get_me()` | `get_me_async()` | `User` (token / connectivity check) |

Common keyword arguments on send methods:

- `chat_id` — override the default destination
- `parse_mode` — omit to use the instance default; pass `None` to force plain text
- `disable_web_page_preview` — text only
- `disable_notification` — silent send
- extra kwargs are forwarded to python-telegram-bot

Limits enforced locally before the HTTP call:

- empty / whitespace-only text → `TelegramConfigError`
- caption > 1024 characters → `TelegramConfigError`
- missing / empty file → `TelegramConfigError`
- document > 50 MB, photo > 10 MB → `TelegramConfigError`

## Listen

Handlers must be `async def callback(update, context)`.

| Method | Effect |
| --- | --- |
| `add_command(name, callback)` | `/name` (slash optional; stored lowercase) |
| `add_text_handler(callback)` | Non-command text |
| `add_handler(handler)` | Raw `telegram.ext` handler |
| `run_polling(...)` | Blocking long poll until Ctrl+C |
| `listen_and_reply` | Alias of `run_polling` |

`run_polling` options:

| Option | Default |
| --- | --- |
| `startup_message` | `None` (no boot ping) |
| `drop_pending_updates` | `True` |
| `poll_timeout` | `20` (getUpdates long-poll seconds) |
| `allowed_updates` | PTB default |
| extra kwargs | forwarded to `Application.run_polling` |

Polling refuses to start if there is no allowlist (`chat_id` /
`allowed_chat_ids`) and `allow_all` is false. Unauthorized chats are ignored
and logged (chat id / user id only, not message text).

Register handlers **before** `run_polling`. After a crash, build a new
`TelegramBot` and register again (see `listener.py`).

## Errors

Re-exported from `telegram.error` so callers can import from one module:

`TelegramError`, `BadRequest`, `ChatMigrated`, `Conflict`, `Forbidden`,
`InvalidToken`, `NetworkError`, `RetryAfter`, `TimedOut`.

Local problems raise `TelegramConfigError` (subclass of `ValueError`): missing
files, bad token shape, empty message, oversize upload, invalid command name.

Retry policy inside `_invoke` (not a public method, documented here for audits):

| Exception | Action |
| --- | --- |
| `RetryAfter` | Sleep `retry_after + 0.5s` unless wait > `max_retry_after` |
| `TimedOut` / `NetworkError` | Exponential backoff, up to `max_retries` |
| `ChatMigrated` | Update default `chat_id` if it matched, retry |
| `BadRequest` parse-entity | Clear `parse_mode` and retry once (`plain_fallback`) |
| `InvalidToken`, `Forbidden`, `Conflict` | Raise immediately |
| Other `TelegramError` | Raise immediately |

## Using `send_alert.py` as a library

`parse_args(argv=None)` is importable (used by tests). `main(argv=None)` returns
an exit code: `0` ok, `2` config / usage, `1` Telegram API error.
