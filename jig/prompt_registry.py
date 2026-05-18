"""PromptRegistry — pairs daemon-side prompt requests with TUI replies.

When the daemon's TuiPromptHandler emits a prompt_request, it generates
a UUID prompt_id and registers a Future under that id. When the client
sends back ``{type: "command", "name": "prompt_reply", "args": [prompt_id, reply]}``,
the dispatcher calls ``registry.deliver(prompt_id, reply)`` which sets
the Future's result and unblocks the handler.

This is correlation-id-based, not session-based: even if multiple TUI
clients are connected, the prompt_id uniquely names the awaiting
prompt regardless of which client replies.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class PromptRegistry:
    _pending: dict[str, asyncio.Future[str]] = field(default_factory=dict)

    def register(self) -> tuple[str, asyncio.Future[str]]:
        """Allocate a new prompt_id and a Future that will resolve to the
        operator's reply text.

        Returns: (prompt_id, future). Caller emits a prompt_request event
        with prompt_id, then ``await future``s.
        """
        prompt_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        self._pending[prompt_id] = future
        return prompt_id, future

    def deliver(self, prompt_id: str, reply: str) -> bool:
        """Resolve the pending prompt with the operator's reply.

        Returns True if the prompt was found and delivered, False if
        the prompt_id is unknown or the future was already resolved
        (late reply, multi-client race, etc.).
        """
        future = self._pending.pop(prompt_id, None)
        if future is None:
            logger.debug("prompt_reply for unknown prompt_id=%s ignored", prompt_id)
            return False
        if future.done():
            logger.debug(
                "prompt_reply for already-resolved prompt_id=%s ignored", prompt_id
            )
            return False
        future.set_result(reply)
        return True

    def cancel(self, prompt_id: str) -> bool:
        """Cancel a pending prompt (e.g., on TuiPromptHandler shutdown).

        Returns True if a pending prompt was cancelled.
        """
        future = self._pending.pop(prompt_id, None)
        if future is None or future.done():
            return False
        future.cancel()
        return True

    def cancel_all(self) -> int:
        """Cancel everything pending (e.g., on server shutdown). Returns count."""
        n = 0
        for future in list(self._pending.values()):
            if not future.done():
                future.cancel()
                n += 1
        self._pending.clear()
        return n
