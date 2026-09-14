# private/ — local secrets

This directory holds the bot token and default chat id. File contents are
**not** committed (see `.gitignore`). Only this README and `.gitkeep` should
appear in git.

## Files the client looks for

| Purpose | Preferred name | Also accepted |
| --- | --- | --- |
| Bot token | `bot_token` | `token`, `telegram_token`, `telegram_bot_token`, or one leftover file that matches BotFather's `digits:secret` shape |
| Default chat id | `chat_id` | `my_chat_id` |

Environment variables override files:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

## Format

One value per file, UTF-8, optional wrapping quotes, optional BOM.

```text
bot_token     123456789:AAE....   (from BotFather)
chat_id       123456789           (user) or -100... (group/channel)
```

Do not put `https://api.telegram.org/bot...` in the token file.

## Rotate

If a token may have leaked, revoke it in [@BotFather](https://t.me/BotFather)
(`/revoke`) and replace the file. Update any environment variables too.
