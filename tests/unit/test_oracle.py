"""Typed oracle coverage for objective and semantic mission assertions."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentic_tester.artifacts.store import ArtifactStore
from agentic_tester.engine.oracle import OracleEngine
from agentic_tester.llm.base import LLMAdapter
from agentic_tester.llm.schemas import AnomalyAssessment, BehaviorVerdict
from agentic_tester.models.mission import ExpectedBehavior
from agentic_tester.models.observation import ConsoleEntry, NetworkEntry, PageObservation


class OracleLLM(LLMAdapter):
    async def plan_next_action(self, observation, mission_context, history_summary, recent_steps):
        raise AssertionError("planning is not used by the oracle")

    async def evaluate_behavior(self, observation, expected, relevant_evidence):
        return BehaviorVerdict(
            verdict="pass",
            confidence=0.8,
            explanation="The semantic content is present.",
        )

    async def assess_anomaly(self, anomaly_description, observation, relevant_evidence):
        return AnomalyAssessment(
            classification="bug",
            title="Observed anomaly",
            description=anomaly_description,
            confidence=0.8,
        )

    async def extract_reproduction_steps(self, anomaly, step_history):
        return []

    async def summarize_history(self, steps):
        return ""

    async def generate_session_summary(self, mission_results):
        return ""


def _observation(
    *,
    console_entries: list[ConsoleEntry] | None = None,
    network_log: list[NetworkEntry] | None = None,
) -> PageObservation:
    return PageObservation(
        url="http://127.0.0.1:8000/projects/1",
        title="Projects",
        screenshot_path="sessions/s1/missions/m1/steps/0000/screenshot.png",
        accessibility_tree='- heading "Projects"',
        interactive_elements=[],
        visible_text_summary="Project created",
        console_entries=console_entries or [],
        network_log=network_log or [],
        state_fingerprint="state-1",
        timestamp=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_oracle_evaluates_network_contract_deterministically(tmp_path: Path) -> None:
    oracle = OracleEngine(OracleLLM(), ArtifactStore(tmp_path / "artifacts"))
    expected = ExpectedBehavior(
        expectation_id="create-request",
        description="POST /api/projects returns 201",
        type="network",
        severity="must",
    )
    observation = _observation(
        network_log=[
            NetworkEntry(
                method="POST",
                url="http://127.0.0.1:8000/api/projects",
                status=201,
                response_body_preview="{}",
                duration_ms=20,
                is_failure=False,
            )
        ]
    )

    evaluation = await oracle.evaluate_expectation(expected, observation)

    assert evaluation.verdict == "pass"
    assert evaluation.confidence == 1.0
    assert evaluation.evaluator_type == "deterministic_network"
    assert evaluation.evidence_ids


@pytest.mark.asyncio
async def test_oracle_reports_console_errors_and_invariant_violations(tmp_path: Path) -> None:
    oracle = OracleEngine(OracleLLM(), ArtifactStore(tmp_path / "artifacts"))
    observation = _observation(
        console_entries=[
            ConsoleEntry(
                level="error",
                text="Uncaught TypeError: save is not a function",
                timestamp=datetime.now(UTC),
            )
        ]
    )
    expected = ExpectedBehavior(
        expectation_id="no-console-errors",
        description="No console errors occur",
        type="error_absence",
        severity="must",
    )

    evaluation = await oracle.evaluate_expectation(expected, observation)
    violations = await oracle.check_invariants(["No console errors"], observation)
    anomalies = await oracle.detect_anomalies(observation)

    assert evaluation.verdict == "fail"
    assert violations == ["No console errors"]
    assert anomalies == ["Console error: Uncaught TypeError: save is not a function"]


@pytest.mark.asyncio
async def test_oracle_uses_llm_for_semantic_state_and_preserves_severity(tmp_path: Path) -> None:
    oracle = OracleEngine(OracleLLM(), ArtifactStore(tmp_path / "artifacts"))
    expected = ExpectedBehavior(
        expectation_id="success-message",
        description="The success message is visible",
        type="ui",
        severity="should",
    )

    evaluation = await oracle.evaluate_expectation(expected, _observation())

    assert evaluation.verdict == "pass"
    assert evaluation.severity == "should"
    assert evaluation.evaluator_type == "llm_semantic"
