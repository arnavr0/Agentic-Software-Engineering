"""Test-session lifecycle model."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from .mission import TestMission
from .safety import ExecutionPolicy


class TestSession(BaseModel):
    """A sequential collection of isolated test missions."""

    session_id: str
    target_base_url: str
    missions: list[TestMission] = Field(default_factory=list)
    execution_policy: ExecutionPolicy = Field(default_factory=ExecutionPolicy)
    config_overrides: dict[str, Any] = Field(default_factory=dict)
    status: Literal["pending", "running", "completed", "failed", "cancelled"] = "pending"
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
