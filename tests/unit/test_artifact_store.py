"""Filesystem artifact and evidence factory tests."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentic_tester.artifacts.store import ArtifactStore
from agentic_tester.models.evidence import EvidenceType
from agentic_tester.models.results import ArtifactPaths, MissionResult, SessionReport, StepResult


def _step(index: int = 0) -> StepResult:
    return StepResult(
        step_index=index,
        step_id=f"step-{index}",
        action_taken="click el_0",
        action_type="click",
        reasoning="test",
        page_url_before="http://localhost:3000",
        page_url_after="http://localhost:3000/projects",
        duration_ms=12,
        timestamp=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_store_persists_all_artifact_types_and_returns_relative_paths(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    screenshot = await store.save_screenshot("session/one", "mission one", 0, b"png")
    step_path = await store.save_step_result("session/one", "mission one", _step())
    mission = MissionResult(
        mission_id="mission one",
        goal="Test",
        status="passed",
        verdict_reasoning="Passed",
        duration_ms=12,
        artifacts=ArtifactPaths(screenshots_dir="screenshots", report_path="report.json"),
    )
    mission_path = await store.save_mission_result("session/one", mission)
    report = SessionReport(
        session_id="session/one",
        target_base_url="http://localhost:3000",
        overall_status="passed",
        summary="Passed",
        total_duration_ms=12,
        artifact_root="artifacts",
        completed_at=datetime.now(UTC),
    )
    report_path = await store.save_session_report(report)
    source_trace = tmp_path / "source-trace.zip"
    source_trace.write_bytes(b"trace")
    trace_path = await store.save_trace("session/one", "mission one", source_trace)
    evidence = store.create_evidence(
        EvidenceType.SCREENSHOT,
        "test",
        0,
        artifact_path=screenshot,
        excerpt="captured",
        width=1280,
    )

    assert screenshot.endswith("screenshot.png")
    assert step_path.endswith("step_result.json")
    assert mission_path.endswith("mission_result.json")
    assert report_path.endswith("report.json")
    assert trace_path.endswith("trace.zip")
    assert evidence.type is EvidenceType.SCREENSHOT
    assert evidence.metadata["width"] == 1280
    assert (tmp_path / "artifacts" / "sessions").is_dir()
    assert (tmp_path / "artifacts" / "sessions" / "session_one" / "missions" / "mission_one").is_dir()


def test_store_rejects_negative_steps_and_missing_traces(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")

    with pytest.raises(ValueError):
        store.get_step_dir("session", "mission", -1)

    with pytest.raises(FileNotFoundError):
        import asyncio

        asyncio.run(store.save_trace("session", "mission", tmp_path / "missing.zip"))
