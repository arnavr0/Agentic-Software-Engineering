"""Session-engine lifecycle and accounting coverage."""

from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

import pytest

from agentic_tester.config import Settings
from agentic_tester.engine import session as session_module
from agentic_tester.llm.base import LLMAdapter
from agentic_tester.llm.schemas import AnomalyAssessment, BehaviorVerdict
from agentic_tester.models.mission import TestMission as MissionModel
from agentic_tester.models.results import ArtifactPaths, MissionResult, TokenUsage
from agentic_tester.models.session import TestSession as SessionModel


class CountingLLM(LLMAdapter):
    def __init__(self) -> None:
        self.token_usage = TokenUsage()

    async def plan_next_action(self, observation, mission_context, history_summary, recent_steps):
        raise AssertionError("planning is stubbed by the fake executor")

    async def evaluate_behavior(self, observation, expected, relevant_evidence):
        return BehaviorVerdict(verdict="pass", confidence=1.0, explanation="stub")

    async def assess_anomaly(self, anomaly_description, observation, relevant_evidence):
        return AnomalyAssessment(classification="observation", confidence=1.0)

    async def extract_reproduction_steps(self, anomaly, step_history):
        return []

    async def summarize_history(self, steps):
        return ""

    async def generate_session_summary(self, mission_results):
        self.token_usage = self.token_usage.model_copy(
            update={
                "input_tokens": self.token_usage.input_tokens + 1,
                "output_tokens": self.token_usage.output_tokens + 1,
                "total_tokens": self.token_usage.total_tokens + 2,
            }
        )
        return "stub summary"


class FakeBrowser:
    def __init__(self) -> None:
        self.is_context_active = False
        self.is_browser_healthy = True
        self.start_count = 0
        self.context_count = 0
        self.closed_contexts = 0
        self.stop_count = 0

    async def start_browser(self, settings) -> None:
        self.start_count += 1

    async def create_context(self, settings) -> None:
        self.context_count += 1
        self.is_context_active = True

    async def close_context(self, trace_path: Path) -> None:
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        trace_path.write_bytes(b"trace")
        self.closed_contexts += 1
        self.is_context_active = False

    async def stop_browser(self) -> None:
        self.stop_count += 1
        self.is_context_active = False


class FakeExecutor:
    calls: ClassVar[list[str]] = []
    failures: ClassVar[set[str]] = set()

    def __init__(self, *, llm, browser, **kwargs) -> None:
        self._llm = llm

    async def execute(self, mission: MissionModel, session_id: str) -> MissionResult:
        self.calls.append(mission.mission_id)
        if mission.mission_id in self.failures:
            raise RuntimeError("deliberate mission failure")
        self._llm.token_usage = self._llm.token_usage.model_copy(
            update={
                "input_tokens": self._llm.token_usage.input_tokens + 10,
                "output_tokens": self._llm.token_usage.output_tokens + 5,
                "total_tokens": self._llm.token_usage.total_tokens + 15,
            }
        )
        return MissionResult(
            mission_id=mission.mission_id,
            goal=mission.goal,
            status="passed",
            verdict_reasoning="stub",
            duration_ms=1,
            artifacts=ArtifactPaths(screenshots_dir="steps", report_path="mission_result.json"),
        )


def _session(tmp_path: Path, missions: list[MissionModel]) -> SessionModel:
    return SessionModel(
        session_id="session-engine",
        target_base_url="http://127.0.0.1:8000",
        missions=missions,
        created_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_session_isolates_missions_orders_them_and_deltas_tokens(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeExecutor.calls = []
    FakeExecutor.failures = set()
    monkeypatch.setattr(session_module, "MissionExecutor", FakeExecutor)
    llm = CountingLLM()
    browser = FakeBrowser()
    engine = session_module.SessionEngine(
        Settings(artifact_dir=tmp_path / "artifacts"),
        browser=browser,
        llm=llm,
    )
    missions = [
        MissionModel(mission_id="low", goal="low", priority="low", target_url="http://127.0.0.1"),
        MissionModel(
            mission_id="critical",
            goal="critical",
            priority="critical",
            target_url="http://127.0.0.1",
        ),
        MissionModel(mission_id="high", goal="high", priority="high", target_url="http://127.0.0.1"),
    ]

    report = await engine.run(_session(tmp_path, missions))

    assert FakeExecutor.calls == ["critical", "high", "low"]
    assert browser.start_count == 1
    assert browser.context_count == browser.closed_contexts == 3
    assert browser.stop_count == 1
    assert [item.token_usage.total_tokens for item in report.mission_results] == [15, 15, 15]
    assert report.total_token_usage.total_tokens == 47
    assert all(item.artifacts.trace_path is not None for item in report.mission_results)
    assert (tmp_path / "artifacts" / "sessions" / "session-engine" / "report.json").is_file()
    assert all(
        (tmp_path / "artifacts" / "sessions" / "session-engine" / "missions" / item / "trace.zip").is_file()
        for item in ("critical", "high", "low")
    )


@pytest.mark.asyncio
async def test_session_continues_after_one_mission_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeExecutor.calls = []
    FakeExecutor.failures = {"first"}
    monkeypatch.setattr(session_module, "MissionExecutor", FakeExecutor)
    engine = session_module.SessionEngine(
        Settings(artifact_dir=tmp_path / "artifacts"),
        browser=FakeBrowser(),
        llm=CountingLLM(),
    )

    report = await engine.run(
        _session(
            tmp_path,
            [
                MissionModel(mission_id="first", goal="first", target_url="http://127.0.0.1"),
                MissionModel(mission_id="second", goal="second", target_url="http://127.0.0.1"),
            ],
        )
    )

    assert FakeExecutor.calls == ["first", "second"]
    assert [item.status for item in report.mission_results] == ["error", "passed"]
    assert report.overall_status == "mixed"
