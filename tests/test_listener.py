from __future__ import annotations

from listener import parse_args


def test_parse_args_default() -> None:
    args = parse_args([])
    assert args.verbose is False


def test_parse_args_verbose() -> None:
    args = parse_args(["-v"])
    assert args.verbose is True
    assert parse_args(["--verbose"]).verbose is True
