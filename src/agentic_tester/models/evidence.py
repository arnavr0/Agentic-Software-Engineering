"""First-class, typed evidence captured during browser testing."""

from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class EvidenceType(str, Enum):
    """Kinds of concrete evidence that can support a tester verdict."""

    SCREENSHOT = "screenshot"
    ACCESSIBILITY = "accessibility"
    CONSOLE = "console"
    NETWORK = "network"
    URL = "url"
    BROWSER_STATE = "browser_state"
    ACTION = "action"
    TRACE = "trace"
    TEXT = "text"


def _utc_now() -> datetime:
    return datetime.now(UTC)


class Evidence(BaseModel):
    """A single piece of concrete browser evidence.

    ``metadata`` is intentionally open-ended because each evidence type carries
    different structured details. It is always instance-local via a factory.
    """

    evidence_id: str = Field(default_factory=lambda: str(uuid4()))
    type: EvidenceType
    source: str
    step_index: int | None = None
    timestamp: datetime = Field(default_factory=_utc_now)
    artifact_path: str | None = None
    excerpt: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
