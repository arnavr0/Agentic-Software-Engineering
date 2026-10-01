"""Validated request and response models for the session API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator, model_validator

from agentic_tester.config import Settings, apply_settings_overrides
from agentic_tester.models.mission import TestMission
from agentic_tester.models.safety import ExecutionPolicy


class CreateSessionRequest(BaseModel):
    """Input contract for one guided tester session."""

    target_base_url: str
    missions: list[TestMission] = Field(min_length=1)
    execution_policy: ExecutionPolicy = Field(default_factory=ExecutionPolicy)
    config_overrides: dict[str, Any] = Field(default_factory=dict)

    @field_validator("target_base_url")
    @classmethod
    def validate_target_base_url(cls, value: str) -> str:
        return _validate_http_url(value, "target_base_url")

    @field_validator("config_overrides")
    @classmethod
    def validate_config_overrides(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            apply_settings_overrides(Settings(), value)
        except (TypeError, ValueError) as exc:
            raise ValueError(str(exc)) from exc
        return value

    @model_validator(mode="after")
    def validate_mission_urls(self) -> CreateSessionRequest:
        mission_ids = [mission.mission_id for mission in self.missions]
        if len(mission_ids) != len(set(mission_ids)):
            raise ValueError("mission_id values must be unique within a session")
        for mission in self.missions:
            _validate_http_url(mission.target_url, f"mission {mission.mission_id!r} target_url")
        return self


class SessionCreatedResponse(BaseModel):
    session_id: str
    status: Literal["pending", "running", "completed", "failed", "cancelled"]
    created_at: datetime


class SessionStatusResponse(BaseModel):
    session_id: str
    status: Literal["pending", "running", "completed", "failed", "cancelled"]
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    overall_status: Literal["passed", "failed", "mixed", "error"] | None = None
    total_bugs: int | None = None
    error: str | None = None


class CancelSessionResponse(BaseModel):
    session_id: str
    status: Literal["pending", "running", "completed", "failed", "cancelled"]


def _validate_http_url(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be an absolute HTTP(S) URL")
    normalized = value.strip()
    if any(char.isspace() for char in normalized):
        raise ValueError(f"{field_name} must be an absolute HTTP(S) URL")
    try:
        parsed = urlsplit(normalized)
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError as exc:
        raise ValueError(f"{field_name} contains an invalid URL") from exc
    if parsed.scheme not in {"http", "https"} or not hostname:
        raise ValueError(f"{field_name} must be an absolute HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f"{field_name} must not contain credentials")
    return normalized
