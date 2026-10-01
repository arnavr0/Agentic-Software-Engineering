"""Purpose-built compact projections for provider requests."""

import json
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel

from agentic_tester.models.evidence import Evidence
from agentic_tester.models.observation import PageObservation
from agentic_tester.models.results import CheckpointResult, MissionResult, StepResult


class ObservationProjection(BaseModel):
    """Semantic browser state needed for planning and oracle decisions."""

    url: str
    title: str
    state_fingerprint: str
    accessibility_tree: str
    interactive_elements: list[dict[str, Any]]
    visible_text: str
    console_entries: list[dict[str, str]]
    network_log: list[dict[str, Any]]


class HistoryProjection(BaseModel):
    """Compact execution fact used for next-action context."""

    step_index: int
    step_id: str | None
    action: str
    action_type: str
    status: str
    page_url_after: str
    error: str | None = None


class EvidenceProjection(BaseModel):
    """Citable evidence without persistence-only metadata."""

    evidence_id: str
    type: str
    source: str
    excerpt: str | None = None


class StepSummaryProjection(BaseModel):
    """Report summary fact used by session synthesis."""

    step_index: int
    step_id: str | None
    action: str
    action_type: str
    status: str
    detail: str | None = None


class CheckpointSummaryProjection(BaseModel):
    """Compact checkpoint verdict used by session synthesis."""

    description: str
    passed: bool
    required: bool
    evaluations: list[dict[str, str | float]]


class BugSummaryProjection(BaseModel):
    """Compact confirmed bug used by session synthesis."""

    title: str
    severity: str
    description: str
    expected_behavior: str
    actual_behavior: str
    reproduction_steps: list[str]
    reproduction_success_rate: str
    confidence: float


class MissionSummaryProjection(BaseModel):
    """Compact mission outcome used by session synthesis."""

    mission_id: str
    goal: str
    status: str
    verdict_reasoning: str
    steps: list[StepSummaryProjection]
    checkpoints: list[CheckpointSummaryProjection]
    final_evaluations: list[dict[str, str | float]]
    invariant_violations: list[str]
    anomalies: list[str]
    bugs: list[BugSummaryProjection]


def observation_projection(
    observation: PageObservation,
    *,
    tree_max_chars: int,
    text_max_chars: int,
) -> ObservationProjection:
    """Project observable state and remove duplicate persistence metadata."""

    tree = observation.accessibility_tree
    marker = "\n# Raw Playwright ARIA snapshot:\n"
    if marker in tree:
        tree = tree.split(marker, 1)[0].rstrip()
    return ObservationProjection(
        url=observation.url,
        title=observation.title,
        state_fingerprint=observation.state_fingerprint,
        accessibility_tree=tree[:tree_max_chars],
        interactive_elements=[
            element.model_dump(include={"id", "role", "name", "is_enabled", "value"}, exclude_none=True)
            for element in observation.interactive_elements
        ],
        visible_text=observation.visible_text_summary[:text_max_chars],
        console_entries=[
            {"level": entry.level, "text": entry.text} for entry in observation.console_entries
        ],
        network_log=[
            {
                "method": entry.method,
                "url": entry.url,
                "status": entry.status,
                "is_failure": entry.is_failure,
            }
            for entry in observation.network_log
        ],
    )


def history_projection(steps: Iterable[StepResult]) -> list[HistoryProjection]:
    """Project execution history without evidence or reasoning payloads."""

    return [
        HistoryProjection(
            step_index=step.step_index,
            step_id=step.step_id,
            action=step.action_taken,
            action_type=step.action_type,
            status="blocked" if step.was_blocked else "error" if step.error else "completed",
            page_url_after=step.page_url_after,
            error=step.error or step.block_reason,
        )
        for step in steps
    ]


def evidence_projection(
    evidence: Iterable[Evidence],
    *,
    excerpt_max_chars: int,
    max_items: int,
) -> list[EvidenceProjection]:
    """Project citable evidence with bounded excerpts and stable ordering."""

    projected: list[EvidenceProjection] = []
    for item in evidence:
        projected.append(
            EvidenceProjection(
                evidence_id=item.evidence_id,
                type=item.type.value,
                source=item.source,
                excerpt=item.excerpt[:excerpt_max_chars] if item.excerpt else None,
            )
        )
        if len(projected) == max_items:
            break
    return projected


def compact_steps(steps: Iterable[StepResult]) -> list[StepSummaryProjection]:
    """Project mission steps for report synthesis."""

    return [
        StepSummaryProjection(
            step_index=step.step_index,
            step_id=step.step_id,
            action=step.action_taken,
            action_type=step.action_type,
            status="blocked" if step.was_blocked else "error" if step.error else "completed",
            detail=step.error or step.block_reason,
        )
        for step in steps
    ]


def compact_checkpoints(checkpoints: Iterable[CheckpointResult]) -> list[CheckpointSummaryProjection]:
    """Project checkpoint outcomes without nested evidence objects."""

    return [
        CheckpointSummaryProjection(
            description=item.checkpoint_description,
            passed=item.passed,
            required=item.required,
            evaluations=[
                {
                    "expectation_id": evaluation.expectation_id,
                    "description": evaluation.description,
                    "verdict": evaluation.verdict,
                    "confidence": evaluation.confidence,
                    "explanation": evaluation.explanation,
                }
                for evaluation in item.expectation_evaluations
            ],
        )
        for item in checkpoints
    ]


def compact_mission(result: MissionResult) -> MissionSummaryProjection:
    """Project one mission result for session synthesis."""

    return MissionSummaryProjection(
        mission_id=result.mission_id,
        goal=result.goal,
        status=result.status,
        verdict_reasoning=result.verdict_reasoning,
        steps=compact_steps(result.steps),
        checkpoints=compact_checkpoints(result.checkpoint_results),
        final_evaluations=[
            {
                "expectation_id": item.expectation_id,
                "description": item.description,
                "severity": item.severity,
                "verdict": item.verdict,
                "confidence": item.confidence,
                "explanation": item.explanation,
            }
            for item in result.final_evaluations
        ],
        invariant_violations=result.invariant_violations,
        anomalies=result.anomalies,
        bugs=[
            BugSummaryProjection(
                title=bug.title,
                severity=bug.severity,
                description=bug.description,
                expected_behavior=bug.expected_behavior,
                actual_behavior=bug.actual_behavior,
                reproduction_steps=[step.action_type for step in bug.reproduction_steps],
                reproduction_success_rate=bug.reproduction_success_rate,
                confidence=bug.confidence,
            )
            for bug in result.bugs
        ],
    )


def compact_json(value: Any) -> str:
    """Serialize model-facing data without indentation or escaped Unicode."""

    if isinstance(value, BaseModel):
        return value.model_dump_json(exclude_none=True)
    if isinstance(value, list):
        return json.dumps(
            [item.model_dump(exclude_none=True) if isinstance(item, BaseModel) else item for item in value],
            ensure_ascii=False,
            separators=(",", ":"),
        )
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
