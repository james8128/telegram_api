from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path

import pytest

import telegram_client as tc
from telegram.error import BadRequest, Forbidden, RetryAfter, TimedOut
from telegram_client import (
    TelegramBot,
    TelegramConfigError,
    TelegramPTB,
    discover_credentials,
    load_secret,
    parse_chat_id,
    parse_token,
    split_message,
)

FAKE_TOKEN = "1234567890:" + ("A" * 35)


def test_alias() -> None:
    assert TelegramPTB is TelegramBot


def test_repr_does_not_leak_token() -> None:
    bot = TelegramBot(token=FAKE_TOKEN, chat_id=1)
    text = repr(bot)
    assert FAKE_TOKEN not in text
    assert "chat_id=1" in text


def test_project_private_files_still_load() -> None:
    token, chat_id = discover_credentials()
    if token is None:
        pytest.skip("no token file in ./private")
    assert parse_token(token) == token
    assert chat_id is not None


def test_parse_token_accepts_valid() -> None:
    assert parse_token(FAKE_TOKEN) == FAKE_TOKEN
    assert parse_token("bot" + FAKE_TOKEN) == FAKE_TOKEN
    assert parse_token(f"  {FAKE_TOKEN}  ") == FAKE_TOKEN


@pytest.mark.parametrize(
    "value",
    [
        "",
        "not-a-token",
        "123:short",
        "https://api.telegram.org/bot" + FAKE_TOKEN,
        FAKE_TOKEN[:-1] + "/",
    ],
)
def test_parse_token_rejects_invalid(value: str) -> None:
    with pytest.raises(TelegramConfigError):
        parse_token(value)


def test_parse_chat_id() -> None:
    assert parse_chat_id(123456789) == 123456789
    assert parse_chat_id("123456789") == 123456789
    assert parse_chat_id("-1001234567890") == -1001234567890
    assert parse_chat_id("@MyChannel") == "@MyChannel"
    with pytest.raises(TelegramConfigError):
        parse_chat_id("")
    with pytest.raises(TelegramConfigError):
        parse_chat_id("not an id")
    with pytest.raises(TelegramConfigError):
        parse_chat_id(True)


def test_split_message_prefers_newlines() -> None:
    text = ("hello\n" * 800) + "tail"
    chunks = split_message(text, limit=4096)
    assert all(len(chunk) <= 4096 for chunk in chunks)
    assert "".join(chunks).replace("\n", "") == text.replace("\n", "")
    assert all(chunk for chunk in chunks)


def test_split_message_hard_cut() -> None:
    text = "a" * 5000
    chunks = split_message(text, 4096)
    assert chunks == ["a" * 4096, "a" * (5000 - 4096)]


def test_split_message_empty() -> None:
    assert split_message("") == []


def test_load_secret_strips_bom_and_quotes(tmp_path: Path) -> None:
    path = tmp_path / "secret"
    path.write_bytes(b"\xef\xbb\xbf\"hello\"\n")
    assert load_secret(path) == "hello"


def test_load_secret_empty(tmp_path: Path) -> None:
    path = tmp_path / "empty"
    path.write_text("   \n", encoding="utf-8")
    with pytest.raises(TelegramConfigError):
        load_secret(path)


def test_discover_named_files(tmp_path: Path) -> None:
    (tmp_path / "bot_token").write_text(FAKE_TOKEN, encoding="utf-8")
    (tmp_path / "chat_id").write_text("42\n", encoding="utf-8")
    token, chat_id = discover_credentials(tmp_path)
    assert token == FAKE_TOKEN
    assert chat_id == 42


def test_discover_legacy_filenames(tmp_path: Path) -> None:
    (tmp_path / "james007_winning_bot").write_text(FAKE_TOKEN, encoding="utf-8")
    (tmp_path / "my_chat_id").write_text("-1001", encoding="utf-8")
    token, chat_id = discover_credentials(tmp_path)
    assert token == FAKE_TOKEN
    assert chat_id == -1001


def test_discover_ambiguous_tokens(tmp_path: Path) -> None:
    other = "9876543210:" + ("B" * 35)
    (tmp_path / "alpha").write_text(FAKE_TOKEN, encoding="utf-8")
    (tmp_path / "beta").write_text(other, encoding="utf-8")
    with pytest.raises(TelegramConfigError, match="Multiple token-like"):
        discover_credentials(tmp_path)


def test_bot_init_requires_token(tmp_path: Path) -> None:
    with pytest.raises(TelegramConfigError, match="No bot token"):
        TelegramBot(private_dir=tmp_path)


def test_bot_autoload_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "99")
    bot = TelegramBot.autoload(private_dir=tmp_path)
    assert bot.token == FAKE_TOKEN
    assert bot.chat_id == 99


def test_bot_env_token_file_chat_id(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "chat_id").write_text("77", encoding="utf-8")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", FAKE_TOKEN)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    bot = TelegramBot.autoload(private_dir=tmp_path)
    assert bot.token == FAKE_TOKEN
    assert bot.chat_id == 77


def test_add_command_validation() -> None:
    bot = TelegramBot(token=FAKE_TOKEN, chat_id=1)

    async def ping(update, context):
        return None

    bot.add_command("/PING", ping)
    with pytest.raises(TelegramConfigError):
        bot.add_command("bad command", ping)
    with pytest.raises(TelegramConfigError):
        bot.add_command("status", "not-callable")  # type: ignore[arg-type]


def test_run_polling_requires_allowlist(tmp_path: Path) -> None:
    bot = TelegramBot(token=FAKE_TOKEN, private_dir=tmp_path)
    with pytest.raises(TelegramConfigError, match="allowlist"):
        bot.run_polling()


def test_empty_message_rejected() -> None:
    bot = TelegramBot(token=FAKE_TOKEN, chat_id=1)
    with pytest.raises(TelegramConfigError, match="empty"):
        asyncio.run(bot.send_async("   "))


def test_missing_chat_id_on_send(tmp_path: Path) -> None:
    bot = TelegramBot(token=FAKE_TOKEN, private_dir=tmp_path)

    async def _send():
        await bot.send_async("hi")

    with pytest.raises(TelegramConfigError, match="No chat id"):
        asyncio.run(_send())


def test_caption_too_long(tmp_path: Path) -> None:
    bot = TelegramBot(token=FAKE_TOKEN, chat_id=1)
    doc = tmp_path / "note.txt"
    doc.write_text("x", encoding="utf-8")
    with pytest.raises(TelegramConfigError, match="Caption"):
        asyncio.run(bot.send_document_async(doc, caption="c" * 1025))


def test_missing_document() -> None:
    bot = TelegramBot(token=FAKE_TOKEN, chat_id=1)
    with pytest.raises(TelegramConfigError, match="not found"):
        asyncio.run(bot.send_document_async("no-such-file.pdf"))


def test_parse_mode_unknown() -> None:
    with pytest.raises(TelegramConfigError, match="parse_mode"):
        TelegramBot(token=FAKE_TOKEN, chat_id=1, parse_mode="bbcode")


def test_redact() -> None:
    bot = TelegramBot(token=FAKE_TOKEN, chat_id=1)
    leaked = f"https://api.telegram.org/bot{FAKE_TOKEN}/sendMessage"
    assert FAKE_TOKEN not in bot._redact(leaked)
    assert "<TOKEN>" in bot._redact(leaked)


def test_sync_send_from_running_loop() -> None:
    bot = TelegramBot(token=FAKE_TOKEN, chat_id=1)

    async def nested():
        bot.send("hi")

    with pytest.raises(RuntimeError, match="already running"):
        asyncio.run(nested())


def test_retry_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = TelegramBot(token=FAKE_TOKEN, chat_id=1, max_retries=3, retry_base=0.01)
    slept: list[float] = []
    calls = {"n": 0}

    async def fake_sleep(delay: float) -> None:
        slept.append(delay)

    async def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise TimedOut("temporarily down")
        return "ok"

    monkeypatch.setattr(tc.asyncio, "sleep", fake_sleep)
    assert asyncio.run(bot._invoke(flaky)) == "ok"
    assert calls["n"] == 3
    assert slept


def test_retry_after_respects_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = TelegramBot(token=FAKE_TOKEN, chat_id=1, max_retry_after=5)

    async def blocked() -> None:
        raise RetryAfter(30)

    with pytest.raises(RetryAfter):
        asyncio.run(bot._invoke(blocked))


def test_retry_after_seconds_handles_int_and_timedelta() -> None:
    class Dummy:
        def __init__(self, retry_after: object) -> None:
            self.retry_after = retry_after

    assert tc._retry_after_seconds(Dummy(3)) == 3.0  # type: ignore[arg-type]
    assert tc._retry_after_seconds(Dummy(timedelta(seconds=2))) == 2.0  # type: ignore[arg-type]


def test_plain_fallback_on_parse_error() -> None:
    bot = TelegramBot(token=FAKE_TOKEN, chat_id=1, parse_mode="Markdown")
    calls: list[str | None] = []

    async def send_message(**kwargs):
        calls.append(kwargs.get("parse_mode"))
        if kwargs.get("parse_mode"):
            raise BadRequest("Can't parse entities: can't find end of the entity")
        return "sent"

    result = asyncio.run(bot._invoke(send_message, chat_id=1, text="*oops", parse_mode="Markdown"))
    assert result == "sent"
    assert calls == ["Markdown", None]


def test_forbidden_is_not_retried() -> None:
    bot = TelegramBot(token=FAKE_TOKEN, chat_id=1)
    calls = {"n": 0}

    async def denied() -> None:
        calls["n"] += 1
        raise Forbidden("bot blocked")

    with pytest.raises(Forbidden):
        asyncio.run(bot._invoke(denied))
    assert calls["n"] == 1


def test_chat_filter_locked_to_default() -> None:
    bot = TelegramBot(token=FAKE_TOKEN, chat_id=42)
    chat_filter = bot._chat_filter()
    assert chat_filter is not None
    bot_open = TelegramBot(token=FAKE_TOKEN, chat_id=42, allow_all=True)
    assert bot_open._chat_filter() is None
