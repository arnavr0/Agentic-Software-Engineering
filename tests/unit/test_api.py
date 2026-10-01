"""API validation, polling, and artifact containment coverage."""

from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from agentic_tester.api.app import create_app
from agentic_tester.api.session_manager import SessionManager
from agentic_tester.artifacts.store import ArtifactStore
from agentic_tester.config import Settings
from agentic_tester.models.results import ArtifactPaths, MissionResult, SessionReport


class ImmediateEngine:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def run(self, session):
        result = MissionResult(
            mission_id=session.missions[0].mission_id,
            goal=session.missions[0].goal,
            status="passed",
            verdict_reasoning="stub",
            duration_ms=1,
            artifacts=ArtifactPaths(screenshots_dir="steps", report_path="mission_result.json"),
        )
        report = SessionReport(
            session_id=session.session_id,
            target_base_url=session.target_base_url,
            overall_status="passed",
            summary="stub",
            mission_results=[result],
            total_duration_ms=1,
            artifact_root=str(self._settings.artifact_dir.resolve()),
            completed_at=datetime.now(UTC),
        )
        session.status = "completed"
        session.completed_at = report.completed_at
        return report


def _payload() -> dict:
    return {
        "target_base_url": "http://127.0.0.1:8000",
        "missions": [
            {
                "mission_id": "m1",
                "goal": "check page",
                "target_url": "http://127.0.0.1:8000/index.html",
                "actions": [],
            }
        ],
    }


def test_api_validates_missions_polls_and_blocks_artifact_escape(tmp_path: Path) -> None:
    settings = Settings(artifact_dir=tmp_path / "artifacts")
    manager = SessionManager(settings, engine_factory=ImmediateEngine)
    client = TestClient(create_app(settings, manager))

    created = client.post("/sessions", json=_payload())
    assert created.status_code == 202
    session_id = created.json()["session_id"]

    status = client.get(f"/sessions/{session_id}")
    assert status.status_code == 200
    assert status.json()["status"] in {"pending", "running", "completed"}
    safe_file = ArtifactStore(settings.artifact_dir).get_session_dir(session_id) / "safe.txt"
    safe_file.write_text("safe", encoding="utf-8")
    assert client.get(f"/sessions/{session_id}/artifacts/safe.txt").status_code == 200

    assert client.get(f"/sessions/{session_id}/artifacts/..%2F..%2Fsecret").status_code in {400, 404}
    invalid = client.post("/sessions", json={**_payload(), "missions": []})
    assert invalid.status_code == 422
    bad_url = client.post("/sessions", json={**_payload(), "target_base_url": "file:///tmp/app"})
    assert bad_url.status_code == 422
    duplicate = _payload()
    duplicate["missions"].append(duplicate["missions"][0].copy())
    assert client.post("/sessions", json=duplicate).status_code == 422
