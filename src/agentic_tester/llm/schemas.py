"""Structured request/response schemas used by LLM adapters."""

from typing import Literal

from pydantic import BaseModel, Field

from agentic_tester.models.mission import ExpectedBehavior


class MissionContext(BaseModel):
    """The mission contract and current guided step supplied to the LLM."""

    mission_id: str
    goal: str
    target_url: str
    current_step_description: str | None = None
    expected_behaviors: list[ExpectedBehavior] = Field(default_factory=list)
    invariants: list[str] = Field(default_factory=list)
    edge_cases: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class BehaviorVerdict(BaseModel):
    """Semantic evaluation of one expected behavior."""

    verdict: Literal["pass", "fail", "uncertain"]
    confidence: float = Field(ge=0.0, le=1.0)
    explanation: str
    evidence_ids: list[str] = Field(default_factory=list)


class AnomalyAssessment(BaseModel):
    """LLM-assisted classification of an observed anomaly."""

    classification: Literal["bug", "suspicious", "observation", "not_anomaly"]
    title: str
    description: str
    expected_behavior: str | None = None
    actual_behavior: str | None = None
    severity: Literal["critical", "major", "minor", "cosmetic", "info"] = "info"
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(default_factory=list)
