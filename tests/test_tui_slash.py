import pytest

from jig.tui.slash import ParsedSlash, SlashParseError, parse_slash


def test_parse_slash_simple():
    assert parse_slash("/help") == ParsedSlash(name="help", args=[])


def test_parse_slash_with_args():
    assert parse_slash("/init dogfood") == ParsedSlash(name="init", args=["dogfood"])


def test_parse_slash_with_quoted_args():
    p = parse_slash('/ticket new --title "set due date"')
    assert p.name == "ticket"
    assert p.args == ["new", "--title", "set due date"]


def test_parse_slash_rejects_non_slash():
    with pytest.raises(SlashParseError, match="not a slash command"):
        parse_slash("init dogfood")


def test_parse_slash_rejects_empty():
    with pytest.raises(SlashParseError, match="empty"):
        parse_slash("/")
    with pytest.raises(SlashParseError, match="empty"):
        parse_slash("/   ")
