"""Example long-running bot.

Register your own commands in make_bot(), then run:

    python listener.py

The loop restarts on unexpected crashes with exponential backoff.
Ctrl+C stops it.
"""

from __future__ import annotations

import logging
import time

from telegram import Update
from telegram.ext import ContextTypes

from telegram_client import TelegramBot, TelegramConfigError

log = logging.getLogger("listener")


async def on_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    await message.reply_text(
        "Commands: /start /ping /status /id\nSend hello for a greeting."
    )


async def on_ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    await message.reply_text("Pong")


async def on_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    await message.reply_text("Active and listening.")


async def on_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None:
        return
    await message.reply_text(f"chat_id: {chat.id}")


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None or not message.text:
        return
    if message.text.strip().lower() == "hello":
        await message.reply_text("Hello")


def make_bot() -> TelegramBot:
    bot = TelegramBot.autoload()
    bot.add_command("start", on_start)
    bot.add_command("ping", on_ping)
    bot.add_command("status", on_status)
    bot.add_command("id", on_id)
    bot.add_text_handler(on_text)
    return bot


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    retry_delay = 5
    while True:
        try:
            bot = make_bot()
            bot.run_polling(startup_message="Bot started and is listening.")
            log.info("Bot stopped.")
            break
        except KeyboardInterrupt:
            log.info("Stopped by user.")
            break
        except TelegramConfigError as exc:
            log.error("Configuration error (not retrying): %s", exc)
            raise
        except Exception:
            log.exception("Listener crashed. Restarting in %ss", retry_delay)
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 60)


if __name__ == "__main__":
    main()
