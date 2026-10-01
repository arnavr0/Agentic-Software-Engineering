
from agentic_tester.engine.mission_executor import MissionExecutor
from agentic_tester.engine.session import _fallback_summary
from agentic_tester.models.results import (
    ArtifactPaths,
    BugReport,
    MissionResult,
    ReplayStep,
    TokenUsage,
)


def _result(status: str = "passed") -> MissionResult:
    return MissionResult(
        mission_id="save-project",
        goal="Save a project",
        status=status,
        verdict_reasoning="Checks passed.",
        duration_ms=10,
        token_usage=TokenUsage(),
        artifacts=ArtifactPaths(screenshots_dir="steps", report_path="report.json"),
    )


def test_minor_bug_does_not_fail_mission_but_is_reported() -> None:
    bug = BugReport(
        bug_id="bug-1",
        title="API health returned 404",
        severity="minor",
        description="The health request failed.",
        expected_behavior="Health returns 200.",
        actual_behavior="Health returned 404.",
        reproduction_steps=[ReplayStep(action_type="click")],
        reproduction_success_rate="2/3",
        confidence=0.9,
    )
    status = MissionExecutor._determine_status([], [], [bug], [])

    assert status == "passed_with_bugs"


def test_fallback_summary_never_omits_bugs() -> None:
    result = _result("passed_with_bugs")
    result.bugs.append(
        BugReport(
            bug_id="bug-1",
            title="API health returned 404",
            severity="minor",
            description="The health request failed.",
            expected_behavior="Health returns 200.",
            actual_behavior="GET /api/health returned HTTP 404.",
            reproduction_steps=[ReplayStep(action_type="click")],
            reproduction_success_rate="2/3",
            confidence=0.85,
        )
    )

    summary = _fallback_summary([result])

    assert "passed_with_bugs" in summary
    assert "API health returned 404" in summary
    assert "HTTP 404" in summary
