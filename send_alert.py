"""One-shot send helper.

Usage:
    python send_alert.py "hello from the bot"
    python send_alert.py --file report.pdf
    python send_alert.py --file report.pdf "optional caption"
    python send_alert.py --chat-id 123456789 "override destination"
"""

from __future__ import annotations

import argparse
import logging
import sys

from telegram_client import TelegramBot, TelegramConfigError, TelegramError


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send a Telegram text message and/or file.")
    parser.add_argument("text", nargs="?", help="Message text")
    parser.add_argument("-f", "--file", dest="file_path", help="Local file to upload as a document")
    parser.add_argument("--photo", dest="photo_path", help="Local image to upload as a photo")
    parser.add_argument("--chat-id", dest="chat_id", help="Override the default chat id")
    parser.add_argument("-v", "--verbose", action="store_true", help="Log HTTP-level detail")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not args.text and not args.file_path and not args.photo_path:
        print("Nothing to send. Pass a message and/or --file / --photo.", file=sys.stderr)
        return 2

    try:
        bot = TelegramBot.autoload()
        extra = {} if args.chat_id is None else {"chat_id": args.chat_id}
        if args.file_path or args.photo_path:
            if args.file_path:
                bot.send_document(args.file_path, caption=args.text or "", **extra)
            if args.photo_path:
                bot.send_photo(args.photo_path, caption=args.text or "", **extra)
        else:
            bot.send(args.text, **extra)
    except (TelegramConfigError, FileNotFoundError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    except TelegramError as exc:
        print(f"Telegram API error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
