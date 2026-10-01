"""Serialization and validation coverage for the domain model layer."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from agentic_tester.models import (
    ActionRisk,
    ActionStep,
    ArtifactPaths,
    Checkpoint,
    Evidence,
    EvidenceType,
    ExecutionPolicy,
    ExpectationEvaluation,
    ExpectedBehavior,
    MissionResult,
)
from agentic_tester.models import (
    TestMission as MissionModel,
)
from agentic_tester.models import (
    TestSession as SessionModel,
)


def _expected(expectation_id: str = "e1") -> ExpectedBehavior:
    return ExpectedBehavior(
        expectation_id=expectation_id,
        description="A success message is visible",
        type="ui",
        severity="must",
    )


def test_mission_round_trips_through_json() -> None:
    mission = MissionModel(
        mission_id="mission-1",
        goal="Create a project",
        target_url="http://localhost:3000/projects",
        actions=[
            ActionStep(
                step_id="submit",
                description="Submit the project form",
                input_data={"name": "Demo"},
                checkpoint=Checkpoint(
                    description="Project was created",
                    expectations=[_expected()],
                ),
            )
        ],
        expected_behaviors=[_expected("e2")],
        invariants=["No 500 responses"],
        edge_cases=["Duplicate project name"],
        tags=["projects"],
        related_routes=["/projects"],
    )

    restored = MissionModel.model_validate_json(mission.model_dump_json())

    assert restored == mission
    assert restored.actions[0].checkpoint is not None
    assert restored.actions[0].checkpoint.expectations[0].expectation_id == "e1"


def test_defaults_are_not_shared_between_model_instances() -> None:
    first_mission = MissionModel(
        mission_id="one", goal="One", target_url="http://localhost:3000"
    )
    second_mission = MissionModel(
        mission_id="two", goal="Two", target_url="http://localhost:3000"
    )
    first_policy = ExecutionPolicy()
    second_policy = ExecutionPolicy()

    first_mission.tags.append("only-first")
    first_policy.allowed_risks.remove(ActionRisk.READ)

    assert second_mission.tags == []
    assert ActionRisk.READ in second_policy.allowed_risks


def test_evidence_generates_unique_identity_and_serializes_timestamp() -> None:
    evidence = Evidence(
        type=EvidenceType.TEXT,
        source="test",
        excerpt="Project created",
        metadata={"content": "Project created"},
    )

    serialized = evidence.model_dump(mode="json")

    assert evidence.evidence_id
    assert evidence.timestamp.tzinfo is not None
    assert serialized["type"] == "text"
    assert isinstance(serialized["timestamp"], str)


def test_results_link_evidence_ids() -> None:
    evidence = Evidence(type=EvidenceType.NETWORK, source="oracle")
    evaluation = ExpectationEvaluation(
        expectation_id="e1",
        description="Create request succeeds",
        verdict="pass",
        confidence=1.0,
        explanation="The request returned 201.",
        evidence_ids=[evidence.evidence_id],
        evaluator_type="network",
    )
    result = MissionResult(
        mission_id="mission-1",
        goal="Create a project",
        status="passed",
        verdict_reasoning="All required checks passed.",
        final_evaluations=[evaluation],
        all_evidence=[evidence],
        duration_ms=42,
        artifacts=ArtifactPaths(
            screenshots_dir="screenshots",
            report_path="report.json",
        ),
    )

    restored = MissionResult.model_validate_json(result.model_dump_json())

    assert restored.final_evaluations[0].evidence_ids == [evidence.evidence_id]
    assert restored.all_evidence[0].type is EvidenceType.NETWORK


def test_invalid_literal_and_confidence_are_rejected() -> None:
    with pytest.raises(ValidationError):
        ExpectedBehavior(
            expectation_id="bad",
            description="Invalid type",
            type="random",
            severity="must",
        )

    with pytest.raises(ValidationError):
        ExpectationEvaluation(
            expectation_id="e1",
            description="Impossible confidence",
            verdict="pass",
            confidence=1.1,
            explanation="Invalid",
            evaluator_type="ui",
        )


def test_session_defaults_to_pending_with_isolated_policy() -> None:
    now = datetime.now(UTC)
    first = SessionModel(session_id="one", target_base_url="http://localhost", created_at=now)
    second = SessionModel(session_id="two", target_base_url="http://localhost", created_at=now)

    first.status = "running"
    first.execution_policy.blocked_url_patterns.append("**/private")

    assert second.status == "pending"
    assert "**/private" not in second.execution_policy.blocked_url_patterns
