"""Tests for jig.tui.clipboard image-from-clipboard helper."""

from unittest.mock import patch

import pytest

from jig.tui.clipboard import ClipboardImageError, get_clipboard_image_bytes


def test_unsupported_platform_raises():
    with patch("jig.tui.clipboard.platform.system", return_value="OS/2"):
        with pytest.raises(ClipboardImageError, match="not supported"):
            get_clipboard_image_bytes()


def test_macos_no_image_in_clipboard_raises(tmp_path):
    """When osascript fails because clipboard has no PNG data, raise the
    user-readable ClipboardImageError."""
    from unittest.mock import MagicMock

    fake_result = MagicMock()
    fake_result.returncode = 1
    fake_result.stderr = "execution error: Can't make «class PNGf» (-1700)"
    with (
        patch("jig.tui.clipboard.platform.system", return_value="Darwin"),
        patch("jig.tui.clipboard.subprocess.run", return_value=fake_result),
    ):
        with pytest.raises(ClipboardImageError, match="does not contain an image"):
            get_clipboard_image_bytes()


def test_macos_returns_image_bytes(tmp_path):
    """Successful path: osascript writes the tempfile, we read it."""
    from unittest.mock import MagicMock

    expected = b"\x89PNG\r\n\x1a\n" + b"x" * 100  # fake png magic + body

    def fake_run(args, **kwargs):
        # The script writes to a tempfile path embedded in the AppleScript
        script = args[2]  # ["osascript", "-e", "<script>"]
        # Find the path in: POSIX file "/tmp/jig-clip-XXXXXX.png"
        import re

        m = re.search(r'POSIX file "([^"]+)"', script)
        assert m, f"no POSIX file in script: {script}"
        target = m.group(1)
        from pathlib import Path

        Path(target).write_bytes(expected)
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        return result

    with (
        patch("jig.tui.clipboard.platform.system", return_value="Darwin"),
        patch("jig.tui.clipboard.subprocess.run", side_effect=fake_run),
    ):
        data = get_clipboard_image_bytes()
    assert data == expected
