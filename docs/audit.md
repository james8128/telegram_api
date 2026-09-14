# Audit notes

Record of the 2026-09-14 hardening pass. Use this when re-auditing, adding
features, or asking why the client behaves a certain way.

## Scope

Self-contained Telegram communication library. Not coupled to any other
project. Callers should be able to import `TelegramBot` and send or listen
without knowing this repo's history.

Stack at audit time:

- Python 3.13.9
- `python-telegram-bot` 22.8
- Windows / PowerShell

## Original issues (pre-1.0.0)

The previous `TelegramPTB` class worked for a basic send and a basic poll
loop. It was not a general library.

| Area | Problem |
| --- | --- |
| Coupling | Default token filename was a personal bot name; `ORDERS_FILE` pointed at an `XAUUSD` orders JSON |
| Lifecycle | `__init__` always built a polling `Application`, including for one-shot `send_alert.py` |
| Parse mode | Default Markdown; ordinary `_` / `*` in alerts could 400 |
| Errors | `send_*` swallowed exceptions and returned `bool` |
| Network | No retry, short implicit HTTP timeouts, module-level `AIORateLimiter` singleton |
| Validation | Token file could be empty; chat id checked only with `isdigit` |
| Limits | No 4096-char split, no 50 MB / 10 MB upload checks |
| Security | Token could appear in logged API URLs; incoming filter existed but handlers assumed `update.message` |
| Files | Open binary handles would be exhausted if a send were retried |
| DX | `print` + emoji; `nest_asyncio` applied globally in `listener.py` |

## What 1.0.0 changed

- One module, `TelegramBot`, with `TelegramPTB` kept as an alias.
- `autoload()` / env vars / `private/` discovery, including the legacy token
  filename so existing files keep working.
- Send path uses `telegram.Bot` as an async context manager (no Application).
- Listen path builds `Application` lazily in `run_polling`.
- Plain text default; optional HTML/Markdown with one plain-text fallback.
- Typed config errors vs Telegram API errors; retries for flood-wait and
  network; no retry on auth / conflict / forbidden.
- Allowlist on incoming traffic; unauthorized updates logged without text.
- Logging + token redaction; `repr` omits the token.
- Examples: generic `send_alert.py` CLI and `listener.py` with crash backoff.
- Unit tests under `tests/` (38 at ship time).

## Design decisions

These are intentional. Do not "fix" them without reading this.

1. **Library stays one file.** Other projects can copy `telegram_client.py` or
   put this folder on `sys.path`. Do not split into a package unless there is
   a second real module.
2. **Secrets stay out of git.** `.gitignore` covers `private/*` and `.env`.
3. **Fail closed on incoming.** Polling without a chat allowlist is refused.
   `allow_all=True` is explicit.
4. **Raise, do not return `False`.** Callers catch `TelegramConfigError` /
   `TelegramError`. Returning bool hid failures.
5. **Sync wrappers exist for scripts; async is the native API.** `send()` uses
   `asyncio.run` and will refuse if a loop is already running.
6. **Default parse mode is `None`.** Formatted messages are opt-in. Broken
   Markdown must never drop an alert.
7. **Uploads pass `pathlib.Path` into PTB** so a retry re-opens the file.
8. **No live Telegram tests in CI.** Fakes cover retry/parse/validation.
   `get_me()` is the manual connectivity check.
9. **Listener rebuilds the client after a crash** so PTB Application state is
   not reused. Config errors do not enter the backoff loop.

## Residual risks / known limits

Not bugs; things a later audit should still know.

- **Two HTTP clients.** Outbound `send()` opens a short-lived `Bot`. Polling
  uses `Application.bot`. Fine for this scale; a future change could share one
  client if send-during-poll becomes hot.
- **No webhook mode.** Long poll only. Fine for a personal/local bot.
- **No persistence / conversation state.** Callers own that.
- **MarkdownV2 is not auto-escaped.** If you set `parse_mode="MarkdownV2"`,
  you must escape. Prefer HTML or plain text.
- **Message splitting can break formatting** across chunk boundaries.
- **`Conflict`** if two processes poll the same token. Listener will backoff
  and retry; stop the extra process.
- **Chat migration** updates `self.chat_id` in memory only. Persist the new id
  yourself if the default chat is a group that upgrades to a supergroup.
- **`RetryAfter.retry_after`** is still numeric in PTB 22.8 and will become
  `timedelta` in a future major. `_retry_after_seconds` accepts both.
- **Rate-limiter extra is optional.** Polling uses `AIORateLimiter` only if
  installed. `send()` retries regardless.
- **Windows file locking.** An upload can fail if another process has the
  file open exclusively.
- **Test `test_project_private_files_still_load`** reads the real `private/`
  token into process memory to check shape. It must never print it.
- **httpx INFO includes the bot token in the request URL.** 1.0.1 installs
  `TokenRedactFilter` on `httpx` / `httpcore` and `configure_logging()` sets
  those loggers to WARNING unless `-v`. Do not raise httpx to INFO in examples.

## Follow-up checklist

Use on the next audit or after a PTB major bump.

- [ ] `python -m pytest tests -q` still green
- [ ] `python-telegram-bot` still in `>=22,<23` or docs/requirements updated
- [ ] Token redaction still covers API URLs in `str(exc)`
- [ ] `RetryAfter.retry_after` still handled (int and timedelta)
- [ ] `private/` still gitignored; no token in the repo
- [ ] `TelegramBot.autoload()` still finds current credential files
- [ ] Manual: `python send_alert.py "audit ping"`
- [ ] Manual: `python listener.py` then `/ping` and `/id` from the allowlisted chat
- [ ] Manual: a second chat must be ignored unless `allow_all=True`
- [ ] If adding commands, keep them in the *caller* (`listener.py` or the
      other project), not in `telegram_client.py`

## How to re-test without spamming Telegram

```text
python -m pytest tests -q
python -c "from telegram_client import TelegramBot; print(repr(TelegramBot.autoload()))"
```

The second line must print `TelegramBot(chat_id=..., allow_all=False, commands=0)`
and must **not** print the token. Then, only if needed:

```text
python -c "from telegram_client import TelegramBot; print(TelegramBot.autoload().get_me().username)"
```
