"""SA<->operator loop — the architecture ask/answer/approval flow.

Bones: exposes the existing SA-conversation + scaffold-approval entry points
(today in ``jig/init_workflow.py``) at their new home. MVP extracts the flow
here, unifies the three SA roles (sa / sa_mvp / sa_v2) into one size-adaptive
role, and writes ``project://arch/...``.
"""

from __future__ import annotations

from jig.init_workflow import (
    apply_scaffold,
    prompt_sa_confirm,
    render_sa_confirm_prompt,
    run_sa_conversation,
)

__all__ = [
    "apply_scaffold",
    "prompt_sa_confirm",
    "render_sa_confirm_prompt",
    "run_sa_conversation",
]
