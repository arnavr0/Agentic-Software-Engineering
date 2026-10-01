"""Structured behavioral contracts supplied to the tester."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class ExpectedBehavior(BaseModel):
    """A behavioral assertion to verify."""

    expectation_id: str
    description: str
    type: Literal["ui", "navigation", "state", "error_absence", "network"]
    severity: Literal["must", "should", "may"]


class Checkpoint(BaseModel):
    """Assertions evaluated immediately after an action step."""

    description: str
    expectations: list[ExpectedBehavior] = Field(default_factory=list)
    invariants: list[str] = Field(default_factory=list)
    required: bool = True
    continue_on_failure: bool = False


class ActionStep(BaseModel):
    """A guided action with an optional mid-execution checkpoint."""

    step_id: str
    description: str
    selector_hint: str | None = None
    input_data: dict[str, Any] = Field(default_factory=dict)
    checkpoint: Checkpoint | None = None


class TestMission(BaseModel):
    """A single testing mission and its behavioral contract."""

    mission_id: str
    goal: str
    priority: Literal["critical", "high", "medium", "low"] = "medium"
    target_url: str
    actions: list[ActionStep] = Field(default_factory=list)
    expected_behaviors: list[ExpectedBehavior] = Field(default_factory=list)
    invariants: list[str] = Field(default_factory=list)
    edge_cases: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    related_routes: list[str] = Field(default_factory=list)
