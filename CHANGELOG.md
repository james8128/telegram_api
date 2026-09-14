# Changelog

## 1.0.0 — 2026-09-14

General-purpose rewrite of the previous `TelegramPTB` helper.

- `TelegramBot` client with `TelegramPTB` alias
- Credential loading from env vars and `private/`, including legacy filenames
- One-shot send via `telegram.Bot` (no polling Application)
- Polling via `run_polling` / `listen_and_reply` with chat allowlist
- Retries for flood-wait and network errors; plain-text fallback on parse errors
- Local checks: token/chat-id shape, 4096-char split, caption/file size limits
- Token redaction in logs and `repr`
- Examples: `send_alert.py`, `listener.py`
- Unit tests in `tests/`

Breaking vs the pre-1.0 helper (this folder was not yet used as a shared lib):

- Default parse mode is plain text, not Markdown
- `send()` returns `list[Message]` and raises on failure (no `bool`)
- `ORDERS_FILE` and hardcoded `/orders` command removed
- Constructor is `TelegramBot(token=..., chat_id=...)`; `autoload()` is preferred
