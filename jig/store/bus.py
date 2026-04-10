import asyncio
import logging
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Awaitable, Callable

from pydantic import ConfigDict, Field

from jig.store.models import StoreModel, TypedCollection

_logger = logging.getLogger(__name__)


class MessageType(str, Enum):
    TASK_ASSIGNMENT = "task_assignment"
    TASK_COMPLETION = "task_completion"
    QUESTION = "question"
    ANSWER = "answer"
    CONTEXT_UPDATE = "context_update"
    STATUS = "status"


class Message(StoreModel):
    sender: str = Field(alias="from")
    to: str
    type: MessageType
    payload: dict
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    correlation_id: str | None = None
    topic: str

    model_config = ConfigDict(populate_by_name=True)
