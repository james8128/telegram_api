# telegram_api

Standalone Telegram bot client for other scripts. It is not tied to any trading
project. Import `telegram_client.TelegramBot` whenever a program needs to send
or receive Telegram messages.

| Item | Value |
| --- | --- |
| Library | [`telegram_client.py`](telegram_client.py) |
| Version | 1.0.1 |
| Runtime | Python 3.10+ (developed on 3.13) |
| Telegram wrapper | `python-telegram-bot` 22.x |
| Tests | `python -m pytest tests` |

Further docs:

- [docs/api.md](docs/api.md) — public API, constructors, errors
- [docs/audit.md](docs/audit.md) — 2026-09-14 audit, design choices, follow-ups
- [CHANGELOG.md](CHANGELOG.md) — version history
- [private/README.md](private/README.md) — credential file names

## Layout

```text
telegram_api/
  telegram_client.py   # the library (import this)
  send_alert.py        # one-shot CLI: send text / file / photo
  listener.py          # example long-running bot
  requirements.txt
  tests/               # unit tests, no live Telegram calls required
  private/             # local secrets (gitignored except README)
  docs/
```

## Setup

```text
pip install -r requirements.txt
```

Optional, for Telegram's flood-wait helper on the polling `Application`:

```text
pip install "python-telegram-bot[rate-limiter]>=22.0,<23"
```

The client works without that extra. Outbound `send()` has its own retry loop.

## Credentials

Never hard-code a token. `TelegramBot.autoload()` resolves each value in this
order (first match wins):

**Token**

1. `TELEGRAM_BOT_TOKEN`
2. `private/bot_token`, then `private/token`, `private/telegram_token`, `private/telegram_bot_token`
3. The only remaining file in `private/` that looks like a BotFather token
   (covers the legacy filename `james007_winning_bot`)

**Chat id**

1. `TELEGRAM_CHAT_ID`
2. `private/chat_id`, then `private/my_chat_id`

Create a bot with [@BotFather](https://t.me/BotFather). Get your numeric chat id
by starting `python listener.py` and sending `/id` to the bot, or by messaging
[@userinfobot](https://t.me/userinfobot).

Recommended files (plain text, no quotes needed):

```text
private/bot_token     # 123456789:AAE...
private/chat_id       # 123456789  or  -1001234567890 for a group
```

`.gitignore` ignores `private/*` except `private/README.md` and
`private/.gitkeep`. Do not commit token files.

## Use from another script

Add this folder to `sys.path`, or copy `telegram_client.py`.

```python
from telegram_client import TelegramBot, TelegramConfigError, TelegramError

bot = TelegramBot.autoload()
try:
    bot.send("hello")
    bot.send_document("report.pdf", caption="latest run")
except TelegramConfigError as exc:
    # missing token, bad chat id, empty file, oversize upload, ...
    raise
except TelegramError as exc:
    # Telegram API / network after retries
    raise
```

From async code, call `send_async` / `send_document_async` / `send_photo_async`.
Do not call the sync helpers from inside a running event loop.

Incoming bot (copy the pattern in `listener.py`):

```python
from telegram import Update
from telegram.ext import ContextTypes
from telegram_client import TelegramBot

async def on_ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message:
        await update.effective_message.reply_text("Pong")

bot = TelegramBot.autoload()
bot.add_command("ping", on_ping)
bot.run_polling(startup_message="online")
```

By default the bot only accepts updates from the configured chat id. Pass
`allow_all=True` only if it must talk to anyone.

Explicit construction, no files:

```python
bot = TelegramBot(token="123:AAE...", chat_id=111)
```

## CLI examples

```text
python send_alert.py "hello"
python send_alert.py --file report.pdf "optional caption"
python send_alert.py --photo chart.png "daily"
python send_alert.py --chat-id 111 "override destination"

python listener.py
python listener.py -v
```

`listener.py` commands: `/start` `/ping` `/status` `/id`. Plain text `hello`
gets `Hello`. Default logs are start/stop and errors — not each long-poll
HTTP call. `-v` logs HTTP requests; the bot token is still redacted.
Unexpected crashes restart with exponential backoff (5s → 60s). Ctrl+C stops.
Config errors are not retried.

## Behaviour that callers should know

- Default parse mode is **plain text**. Markdown/HTML is opt-in (`parse_mode=`).
  If Telegram rejects entities, the client retries once as plain text.
- Text longer than 4096 characters is split (newline, then space, then hard cut).
  `send()` returns a list of `Message` objects, one per chunk.
- Documents max 50 MB; photos max 10 MB; captions max 1024 characters.
- Flood-wait (`RetryAfter`) and network timeouts are retried. `Forbidden`,
  `InvalidToken`, and `Conflict` are not.
- Tokens are redacted in log lines (including httpx URLs). `repr(bot)` does
  not include the token.

## Tests

```text
python -m pytest tests -q
```

These are local unit tests (validation, retries with fakes, CLI argparse).
They do not send to Telegram. One test loads `./private` if present and only
checks that the token *shape* is valid; it does not print the secret.

## Security

- Keep `private/` and `.env` off git and off shared drives.
- Treat the bot token as a password. Rotate it in BotFather if it leaks.
- Keep `allow_all=False` unless you have a reason to accept strangers.
- Do not log `bot.token` or raw Telegram API URLs (they embed the token).
