from __future__ import annotations

from send_alert import parse_args


def test_parse_args_text_only() -> None:
    args = parse_args(["hello world"])
    assert args.text == "hello world"
    assert args.file_path is None


def test_parse_args_file_and_caption() -> None:
    args = parse_args(["--file", "report.pdf", "latest orders"])
    assert args.file_path == "report.pdf"
    assert args.text == "latest orders"


def test_parse_args_verbose() -> None:
    args = parse_args(["-v", "hello"])
    assert args.verbose is True
    assert args.text == "hello"
