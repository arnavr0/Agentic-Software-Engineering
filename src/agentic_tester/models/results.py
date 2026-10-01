"""Machine-readable mission results and evidence-linked verdicts."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from .element import ElementRef
from .evidence import Evidence


class ExpectationEvaluation(BaseModel):
    """Oracle verdict for one expected behavior, linked to evidence IDs."""

    expectation_id: str
    description: str
    severity: Literal["must", "should", "may"] = "must"
    verdict: Literal["pass", "fail", "uncertain", "skip"]
    confidence: float = Field(ge=0.0, le=1.0)
    explanation: str
    evidence_ids: list[str] = Field(default_factory=list)
    evaluator_type: str


class StepResult(BaseModel):
    """Result of one action step."""

    step_index: int
    step_id: str | None = None
    action_taken: str
    action_type: str
    reasoning: str
    evidence: list[Evidence] = Field(default_factory=list)
    page_url_before: str
    page_url_after: str
    duration_ms: int
    timestamp: datetime
    was_blocked: bool = False
    block_reason: str | None = None
    error: str | None = None


class CheckpointResult(BaseModel):
    """Result of evaluating a mid-execution checkpoint."""

    checkpoint_description: str
    passed: bool
    required: bool
    expectation_evaluations: list[ExpectationEvaluation] = Field(default_factory=list)
    invariant_violations: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)


class ReplayStep(BaseModel):
    """Executable action record for deterministic reproduction."""

    action_type: str
    element_ref: ElementRef | None = None
    value: str | None = None
    key: str | None = None
    expected_state_fingerprint: str | None = None


class BugReport(BaseModel):
    """Confirmed or suspected bug with its supporting evidence chain."""

    bug_id: str
    title: str
    severity: Literal["critical", "major", "minor", "cosmetic"]
    description: str
    expected_behavior: str
    actual_behavior: str
    reproduction_steps: list[ReplayStep] = Field(default_factory=list)
    reproduction_steps_readable: list[str] = Field(default_factory=list)
    reproduction_success_rate: str
    evidence: list[Evidence] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class TokenUsage(BaseModel):
    """LLM token accounting for one result or a whole session."""

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)


class ArtifactPaths(BaseModel):
    """Paths to artifacts emitted while executing a mission."""

    trace_path: str | None = None
    screenshots_dir: str
    report_path: str


class MissionResult(BaseModel):
    """Complete result of executing one mission."""

    mission_id: str
    goal: str
    status: Literal["passed", "passed_with_bugs", "failed", "uncertain", "error"]
    verdict_reasoning: str
    steps: list[StepResult] = Field(default_factory=list)
    checkpoint_results: list[CheckpointResult] = Field(default_factory=list)
    final_evaluations: list[ExpectationEvaluation] = Field(default_factory=list)
    invariant_violations: list[str] = Field(default_factory=list)
    anomalies: list[str] = Field(default_factory=list)
    bugs: list[BugReport] = Field(default_factory=list)
    all_evidence: list[Evidence] = Field(default_factory=list)
    duration_ms: int
    token_usage: TokenUsage = Field(default_factory=TokenUsage)
    artifacts: ArtifactPaths


class SessionReport(BaseModel):
    """Complete report for an entire test session."""

    session_id: str
    target_base_url: str
    overall_status: Literal["passed", "failed", "mixed", "error"]
    summary: str
    mission_results: list[MissionResult] = Field(default_factory=list)
    total_bugs: int = Field(default=0, ge=0)
    total_duration_ms: int
    total_token_usage: TokenUsage = Field(default_factory=TokenUsage)
    artifact_root: str
    completed_at: datetime
