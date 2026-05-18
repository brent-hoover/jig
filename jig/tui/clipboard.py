"""Read images from the system clipboard.

macOS-first via ``osascript``. Linux fallback via ``xclip`` /
``wl-paste`` if installed. Returns the raw bytes (PNG-encoded) or
None on failure.

Used by ``Ctrl+I`` in the Composer to paste a screenshot reference
into the input.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
from pathlib import Path


class ClipboardImageError(RuntimeError):
    """Raised when the clipboard doesn't contain an image (or the platform
    doesn't support image paste)."""


def get_clipboard_image_bytes() -> bytes:
    """Return PNG bytes of the current clipboard image, or raise.

    Raises ``ClipboardImageError`` with a user-readable message if the
    clipboard doesn't contain an image, the platform isn't supported,
    or the helper tool is missing.
    """
    system = platform.system()
    if system == "Darwin":
        return _macos_clipboard_image()
    if system == "Linux":
        return _linux_clipboard_image()
    raise ClipboardImageError(f"clipboard image paste not supported on {system}")


def _macos_clipboard_image() -> bytes:
    """macOS: ``osascript`` extracts the clipboard's PNG data and writes
    it to a tempfile we read."""
    import tempfile

    with tempfile.NamedTemporaryFile(
        suffix=".png", prefix="jig-clip-", delete=False
    ) as tmp:
        tmp_path = tmp.name

    try:
        # AppleScript: read the clipboard as «class PNGf» and write
        # to the tempfile. `osascript` returns non-zero if the
        # clipboard doesn't have image data of that class.
        script = (
            "set imgData to the clipboard as «class PNGf»\n"
            f'set out to (open for access POSIX file "{tmp_path}" '
            "with write permission)\n"
            "set eof of out to 0\n"
            "write imgData to out\n"
            "close access out\n"
        )
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode != 0:
            stderr = result.stderr.strip().lower()
            if (
                "can’t make" in stderr
                or "can't make" in stderr
                or "class pngf" in stderr
            ):
                raise ClipboardImageError("clipboard does not contain an image")
            raise ClipboardImageError(
                f"osascript failed (rc={result.returncode}): {result.stderr.strip()}"
            )
        data = Path(tmp_path).read_bytes()
        if not data:
            raise ClipboardImageError("clipboard image was empty")
        return data
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def _linux_clipboard_image() -> bytes:
    """Linux: try wl-paste (Wayland) first, then xclip (X11)."""
    if shutil.which("wl-paste"):
        result = subprocess.run(
            ["wl-paste", "--type", "image/png"],
            capture_output=True,
            timeout=3,
        )
        if result.returncode == 0 and result.stdout:
            return result.stdout
        raise ClipboardImageError("clipboard does not contain an image")
    if shutil.which("xclip"):
        result = subprocess.run(
            ["xclip", "-selection", "clipboard", "-t", "image/png", "-o"],
            capture_output=True,
            timeout=3,
        )
        if result.returncode == 0 and result.stdout:
            return result.stdout
        raise ClipboardImageError("clipboard does not contain an image")
    raise ClipboardImageError(
        "no clipboard helper found; install wl-paste (Wayland) or xclip (X11)"
    )
