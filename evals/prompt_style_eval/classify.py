"""Outcome classification from a persisted transcript.

The classifier sees only what the SDK wrapper returns; ``error`` and
``timeout`` are pre-classified by the runner (which sees SDK exceptions and
wall-clock overruns directly). The classifier therefore picks among:

    malformed → refusal → code → question → malformed

Priority order matters and is intentional: refusal *with* a code block is
still a refusal; code *with* a trailing question mark is still code (the
model produced a usable answer, and we don't penalize chatty endings).

The classifier is heuristic. Mistakes are recoverable because the raw
transcript is persisted — we can replay the classifier over the store
after fixing rules. Mistakes should be roughly evenly distributed across
prompt styles for v1; if they cluster, the classifier is biasing the
comparison and needs attention.
"""

from __future__ import annotations

import re
from typing import Literal


# Subset of Outcome that the classifier can return. The runner promotes
# transport-level signals (SDK exception, wall-clock timeout) to
# ``error``/``timeout`` before calling here.
PostHocOutcome = Literal["code", "question", "refusal", "malformed"]


_CODE_FENCE_RE = re.compile(r"```[a-zA-Z0-9_+\-]*\n(.*?)```", re.DOTALL)

# Prefixes (lowercased, leading whitespace stripped) that mark a refusal.
# Order doesn't matter — startswith match.
_REFUSAL_PREFIXES: tuple[str, ...] = (
    "i can't",
    "i cannot",
    "i won't",
    "i will not",
    "i'm not able",
    "i am not able",
    "i'm unable",
    "i am unable",
    "sorry, i can't",
    "sorry, i cannot",
    "sorry, but i",
)


def extract_assistant_text(transcript: list[dict]) -> str | None:
    """Concat ``TextBlock`` text from the *last* ``AssistantMessage``.

    Returns ``None`` if there is no assistant message in the transcript, or if
    the only assistant message contributed no text blocks (e.g. tool-use only
    — irrelevant for v1 since we run with ``allowed_tools=[]``, but the
    classifier shouldn't crash on it).
    """
    assistant_messages = [m for m in transcript if m.get("type") == "AssistantMessage"]
    if not assistant_messages:
        return None
    last = assistant_messages[-1]
    content = last.get("content") or []
    text_parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "TextBlock":
            text_parts.append(block.get("text", ""))
    if not text_parts:
        return None
    return "".join(text_parts)


def extract_code(text: str) -> str | None:
    """Return the concatenated contents of all fenced code blocks, or ``None``.

    Accepts any language tag (or none). Blocks are joined by ``\\n\\n`` to
    keep them syntactically separable when later written to disk for the
    sandbox.
    """
    blocks = _CODE_FENCE_RE.findall(text)
    if not blocks:
        return None
    cleaned = [block.rstrip("\n") for block in blocks]
    return "\n\n".join(cleaned)


_FENCE_WITH_POS_RE = re.compile(
    r"```([a-zA-Z0-9_+\-]*)\n(.*?)```", re.DOTALL
)


def _filename_from_heading(text: str) -> str | None:
    """Inspect the last non-blank line of ``text`` for a filename heading.

    Matches markdown patterns the model is likely to produce:
    ``**app.py**``, ``### app.py``, ``## app.py``, ``# app.py``,
    ``File: app.py``, ``app.py:``.
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return None
    candidate = lines[-1]
    patterns = (
        r"^\*\*([a-zA-Z0-9_./-]+\.py)\*\*\s*$",
        r"^#{1,4}\s+([a-zA-Z0-9_./-]+\.py)\s*$",
        r"^(?:File|Filename):\s*([a-zA-Z0-9_./-]+\.py)\s*$",
        r"^([a-zA-Z0-9_./-]+\.py)\s*:\s*$",
    )
    for pattern in patterns:
        match = re.match(pattern, candidate, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def _filename_from_first_line(code: str) -> str | None:
    """If the first line of the code is ``# app.py``, return ``"app.py"``."""
    first = code.splitlines()[0].strip() if code else ""
    if not first.startswith("#"):
        return None
    rest = first.lstrip("#").strip()
    if not rest:
        return None
    first_token = rest.split()[0]
    if re.fullmatch(r"[a-zA-Z0-9_./-]+\.py", first_token):
        return first_token
    return None


def extract_files(text: str, *, default_filename: str) -> dict[str, str]:
    """Extract one or more named files from a model response.

    Each fenced code block is associated with a filename in priority order:
    (1) a markdown heading or ``**bold**`` filename immediately preceding it,
    (2) a ``# filename.py`` comment on the first line of the block,
    (3) ``default_filename`` for the first unlabeled block.

    Subsequent unlabeled blocks are dropped — the model is expected to label
    its files when emitting more than one. Returns an empty dict when no code
    blocks are present.
    """
    files: dict[str, str] = {}
    cursor = 0
    default_used = False
    for match in _FENCE_WITH_POS_RE.finditer(text):
        preceding = text[cursor:match.start()]
        body = match.group(2).rstrip("\n")

        filename = (
            _filename_from_heading(preceding)
            or _filename_from_first_line(body)
        )
        if filename is None and not default_used:
            filename = default_filename
            default_used = True
        if filename is None:
            cursor = match.end()
            continue

        files[filename] = body
        cursor = match.end()
    return files


def _looks_like_refusal(text: str) -> bool:
    head = text.lstrip().lower()
    return any(head.startswith(prefix) for prefix in _REFUSAL_PREFIXES)


def _looks_like_question(text: str) -> bool:
    return text.rstrip().endswith("?")


def classify(transcript: list[dict]) -> PostHocOutcome:
    """Decide which outcome bucket the run lands in (post-success).

    The runner handles SDK errors and timeouts before calling here.
    """
    text = extract_assistant_text(transcript)
    if text is None:
        return "malformed"
    if _looks_like_refusal(text):
        return "refusal"
    if extract_code(text) is not None:
        return "code"
    if _looks_like_question(text):
        return "question"
    return "malformed"
