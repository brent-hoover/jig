"""LLM-as-judge scoring with a frozen rubric checklist.

The judge is one of three quality signals (alongside ruff findings and
cyclomatic complexity). Its output is structured — a JSON object mapping
rubric item ids to booleans or integers — so it can be aggregated across
runs without an extraction layer. Free-form judge prose was explicitly
rejected during design (see Alternatives in design.md).

Failure modes are explicit: ``JudgeError`` covers every reason the judge
could fail to produce a valid checklist. Callers (the runner) catch this
and persist a code-outcome run with ``judge=None``, since judge failure
shouldn't invalidate the candidate's own measurements.
"""

from __future__ import annotations

import json

from jig.evals.prompt_style_eval.classify import extract_assistant_text
from jig.evals.prompt_style_eval.models import JudgeScore, Rubric
from jig.evals.prompt_style_eval.sdk import invoke


class JudgeError(Exception):
    """The judge produced unparseable, malformed, or invalid output."""


def build_judge_prompt(code: str, rubric: Rubric) -> str:
    """Construct the user message sent to the judge model."""
    lines = [
        "You are evaluating Python code against a fixed checklist. Read the code "
        "below, then answer each item.",
        "",
        "CODE TO EVALUATE:",
        "```python",
        code.rstrip("\n"),
        "```",
        "",
        "CHECKLIST:",
    ]
    for item in rubric.items:
        scale_hint = (
            "true/false"
            if item.scale == "bool"
            else "integer 1-5 (1=very poor, 5=excellent)"
        )
        lines.append(f"- {item.id} ({scale_hint}): {item.prompt}")
    lines.extend(
        [
            "",
            "INSTRUCTIONS:",
            "- Return a single JSON object mapping each item id to its answer.",
            "- For bool items: true or false.",
            "- For likert_5 items: integer 1-5.",
            "- Output ONLY the JSON object. No prose, no markdown fences, no commentary.",
            "",
            f'Example: {{"{rubric.items[0].id}": '
            f"{'true' if rubric.items[0].scale == 'bool' else '4'}}}",
        ]
    )
    return "\n".join(lines)


def _strip_fences(text: str) -> str:
    """If the judge wrapped its JSON in a fenced block despite instructions,
    unwrap it. Otherwise, return as-is."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return text
    body = stripped[3:]
    if "\n" in body:
        first_line, rest = body.split("\n", 1)
        # If the language tag line is non-empty (e.g. ```json), drop it;
        # otherwise the first newline was just after the fence.
        body = rest if first_line.strip() else rest
    body = body.rstrip()
    if body.endswith("```"):
        body = body[:-3]
    return body


def parse_judge_response(text: str, rubric: Rubric) -> dict[str, bool | int]:
    """Parse the judge's text into a validated checklist.

    Raises ``JudgeError`` for unparseable JSON, missing rubric items, wrong
    value types, or out-of-range likert scores.
    """
    cleaned = _strip_fences(text).strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise JudgeError(f"could not parse JSON: {exc.msg}") from exc
    if not isinstance(data, dict):
        raise JudgeError(f"expected JSON object, got {type(data).__name__}")

    result: dict[str, bool | int] = {}
    for item in rubric.items:
        if item.id not in data:
            raise JudgeError(f"missing rubric item: {item.id}")
        value = data[item.id]
        if item.scale == "bool":
            if not isinstance(value, bool):
                raise JudgeError(
                    f"{item.id}: expected bool, got {type(value).__name__}"
                )
            result[item.id] = value
        else:  # likert_5
            if not isinstance(value, int) or isinstance(value, bool):
                raise JudgeError(
                    f"{item.id}: expected int 1-5, got {type(value).__name__}"
                )
            if not 1 <= value <= 5:
                raise JudgeError(f"{item.id}: likert value {value} out of range 1-5")
            result[item.id] = value
    return result


async def score(code: str, rubric: Rubric, *, model: str) -> JudgeScore:
    """Run the judge on a candidate code snippet and return a structured score.

    Raises ``JudgeError`` if the judge response cannot be parsed into a valid
    checklist for ``rubric``. The judge's tokens and cost are recorded on the
    returned ``JudgeScore`` for cost accounting.
    """
    prompt = build_judge_prompt(code, rubric)
    invocation = await invoke(prompt, model=model)
    text = extract_assistant_text(invocation.transcript)
    if text is None:
        raise JudgeError("judge produced no assistant text")
    checklist = parse_judge_response(text, rubric)
    return JudgeScore(
        model=model,
        rubric_version=rubric.version,
        checklist=checklist,
        tokens=invocation.tokens,
        cost_usd=invocation.cost_usd,
    )
