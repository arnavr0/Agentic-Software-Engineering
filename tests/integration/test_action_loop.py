"""End-to-end action-loop test with a deterministic LLM stub and local fixture app."""

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from agentic_tester.agent.action_loop import ActionLoop
from agentic_tester.agent.action_space import ActionDecision, ActionType
from agentic_tester.agent.context_manager import ContextManager
from agentic_tester.agent.guardrails import GuardrailsEngine
from agentic_tester.artifacts.store import ArtifactStore
from agentic_tester.browser.perception import PerceptionEngine
from agentic_tester.browser.worker import BrowserWorker
from agentic_tester.config import Settings
from agentic_tester.engine.mission_executor import MissionExecutor
from agentic_tester.engine.oracle import OracleEngine
from agentic_tester.llm.base import LLMAdapter
from agentic_tester.llm.schemas import (
    AnomalyAssessment,
    BehaviorVerdict,
    MissionContext,
)
from agentic_tester.models.element import ElementRef
from agentic_tester.models.evidence import EvidenceType
from agentic_tester.models.mission import ActionStep, Checkpoint, ExpectedBehavior
from agentic_tester.models.mission import TestMission as MissionModel
from agentic_tester.models.results import ReplayStep, StepResult
from agentic_tester.models.safety import ExecutionPolicy

FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "test_app"


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:
        return


class DeterministicLLM(LLMAdapter):
    """Fake provider that makes the fixture mission fully deterministic."""

    async def plan_next_action(
        self,
        observation,
        mission_context,
        history_summary: str,
        recent_steps: list[StepResult],
    ) -> ActionDecision:
        description = (mission_context.current_step_description or "").casefold()
        if "fill" in description:
            element = next(item for item in observation.interactive_elements if item.role == "textbox")
            return ActionDecision(
                action_type=ActionType.FILL,
                ref=element.id,
                value="Loop project",
                reasoning="Fill the project name field.",
                confidence=1.0,
                expected_result="The project name is entered.",
            )
        element = next(item for item in observation.interactive_elements if item.name == "Save project")
        return ActionDecision(
            action_type=ActionType.CLICK,
            ref=element.id,
            reasoning="Submit the project form.",
            confidence=1.0,
            expected_result="The saved status appears.",
        )

    async def evaluate_behavior(self, observation, expected, relevant_evidence) -> BehaviorVerdict:
        return BehaviorVerdict(verdict="pass", confidence=1.0, explanation="Stub verdict.")

    async def assess_anomaly(self, anomaly_description, observation, relevant_evidence) -> AnomalyAssessment:
        return AnomalyAssessment(
            classification="observation",
            title="Stub observation",
            description=anomaly_description,
            confidence=1.0,
        )

    async def extract_reproduction_steps(self, anomaly: str, step_history) -> list[str]:
        return [step.action_taken for step in step_history]

    async def summarize_history(self, steps: list[StepResult]) -> str:
        return f"{len(steps)} prior steps"

    async def generate_session_summary(self, mission_results) -> str:
        return f"{len(mission_results)} missions"


@pytest.fixture
def fixture_server():
    handler = partial(QuietHandler, directory=str(FIXTURE_DIR))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/index.html"
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


@pytest.mark.asyncio
async def test_action_loop_persists_evidence_and_replays_actions(fixture_server, tmp_path: Path) -> None:
    settings = Settings(headless=True, default_timeout_ms=5_000, max_retries_per_action=0)
    browser = BrowserWorker()
    store = ArtifactStore(tmp_path / "artifacts")
    llm = DeterministicLLM()
    loop = ActionLoop(
        llm=llm,
        browser=browser,
        perception=PerceptionEngine(),
        guardrails=GuardrailsEngine(ExecutionPolicy()),
        context_mgr=ContextManager(window_size=2, llm=llm),
        artifact_store=store,
        settings=settings,
        session_id="session-1",
        mission_id="mission-1",
    )
    context = MissionContext(
        mission_id="mission-1",
        goal="Create a project",
        target_url=fixture_server,
        expected_behaviors=[
            ExpectedBehavior(
                expectation_id="e1",
                description="Project saved appears",
                type="ui",
                severity="must",
            )
        ],
    )

    await browser.start_browser(settings)
    try:
        await browser.create_context(settings)
        await browser.navigate(fixture_server)
        filled = await loop.run_guided_step(
            step=_action_step("fill", "Fill the project name"),
            mission_context=context,
            step_index=0,
        )
        clicked = await loop.run_guided_step(
            step=_action_step("save", "Click Save project"),
            mission_context=context,
            step_index=1,
        )
        replayed = await loop.replay_step(
            ReplayStep(
                action_type="click",
                element_ref=ElementRef(
                    id="el_2",
                    role="button",
                    name="Save project",
                    locator_value="#save",
                ),
            ),
            step_index=2,
        )

        assert filled.error is None
        assert clicked.error is None
        assert replayed.error is None
        assert filled.action_type == "fill"
        assert clicked.action_type == "click"
        assert replayed.action_type == "click"
        assert any(item.type is EvidenceType.ACTION for item in clicked.evidence)
        assert any(item.type is EvidenceType.NETWORK for item in clicked.evidence)
        assert len(loop.get_step_history()) == 2
        assert loop.last_step_result is not None
        assert (
            tmp_path
            / "artifacts"
            / "sessions"
            / "session-1"
            / "missions"
            / "mission-1"
            / "steps"
            / "0001"
            / "step_result.json"
        ).is_file()
    finally:
        await browser.stop_browser()


@pytest.mark.asyncio
async def test_mission_executor_runs_guided_steps_and_saves_report(
    fixture_server,
    tmp_path: Path,
) -> None:
    settings = Settings(
        headless=True,
        default_timeout_ms=5_000,
        max_retries_per_action=0,
        artifact_dir=tmp_path / "artifacts",
    )
    browser = BrowserWorker()
    store = ArtifactStore(settings.artifact_dir)
    llm = DeterministicLLM()
    mission = MissionModel(
        mission_id="mission-executor",
        goal="Save a project",
        target_url=fixture_server,
        actions=[
            ActionStep(step_id="fill", description="Fill the project name"),
            ActionStep(
                step_id="save",
                description="Click Save project",
                checkpoint=Checkpoint(
                    description="The saved status is visible",
                    expectations=[
                        ExpectedBehavior(
                            expectation_id="saved-status",
                            description="Project saved appears",
                            type="ui",
                            severity="must",
                        )
                    ],
                ),
            ),
        ],
        expected_behaviors=[
            ExpectedBehavior(
                expectation_id="final-status",
                description="Project saved remains visible",
                type="ui",
                severity="must",
            )
        ],
    )
    executor = MissionExecutor(
        llm=llm,
        browser=browser,
        perception=PerceptionEngine(),
        oracle=OracleEngine(llm, store),
        investigator=None,
        guardrails=GuardrailsEngine(ExecutionPolicy()),
        artifact_store=store,
        settings=settings,
    )

    result = await executor.execute(mission, session_id="session-executor")

    assert result.status == "passed"
    assert [step.step_id for step in result.steps] == ["fill", "save"]
    assert result.checkpoint_results[0].passed is True
    assert result.final_evaluations[0].verdict == "pass"
    assert result.artifacts.trace_path is not None
    assert (
        settings.artifact_dir
        / "sessions"
        / "session-executor"
        / "missions"
        / "mission-executor"
        / "mission_result.json"
    ).is_file()
    await browser.stop_browser()


def _action_step(step_id: str, description: str):
    return ActionStep(step_id=step_id, description=description)
