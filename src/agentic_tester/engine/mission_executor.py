"""Checkpoint-aware execution of one guided test mission."""

import time
from collections.abc import Callable
from pathlib import Path

from agentic_tester.agent.action_loop import ActionLoop
from agentic_tester.agent.context_manager import ContextManager
from agentic_tester.agent.guardrails import GuardrailsEngine
from agentic_tester.agent.investigator import BugInvestigator
from agentic_tester.artifacts.store import ArtifactStore
from agentic_tester.browser.perception import PerceptionEngine
from agentic_tester.browser.worker import BrowserWorker
from agentic_tester.config import Settings
from agentic_tester.engine.oracle import OracleEngine
from agentic_tester.llm.base import LLMAdapter
from agentic_tester.llm.schemas import MissionContext
from agentic_tester.models.evidence import Evidence
from agentic_tester.models.mission import TestMission
from agentic_tester.models.results import (
    ArtifactPaths,
    BugReport,
    CheckpointResult,
    ExpectationEvaluation,
    MissionResult,
    ReplayStep,
    StepResult,
    TokenUsage,
)


class MissionExecutor:
    """Execute one mission and compile its evidence-backed result."""

    def __init__(
        self,
        llm: LLMAdapter,
        browser: BrowserWorker,
        perception: PerceptionEngine,
        oracle: OracleEngine,
        investigator: BugInvestigator | None,
        guardrails: GuardrailsEngine,
        artifact_store: ArtifactStore,
        settings: Settings,
        investigator_factory: Callable[[ActionLoop], BugInvestigator] | None = None,
    ) -> None:
        self._llm = llm
        self._browser = browser
        self._perception = perception
        self._oracle = oracle
        self._investigator = investigator
        self._guardrails = guardrails
        self._artifact_store = artifact_store
        self._settings = settings
        self._investigator_factory = investigator_factory

    async def execute(self, mission: TestMission, session_id: str) -> MissionResult:
        """Run a mission, isolate its context when necessary, and save its report."""

        started = time.perf_counter()
        steps: list[StepResult] = []
        replay_steps: list[ReplayStep] = []
        checkpoint_results: list[CheckpointResult] = []
        final_evaluations: list[ExpectationEvaluation] = []
        invariant_violations: list[str] = []
        bugs: list[BugReport] = []
        anomalies: list[str] = []
        status: str = "error"
        verdict_reasoning = "Mission did not complete."
        own_context = False
        trace_path = self._artifact_store.get_mission_dir(session_id, mission.mission_id) / "trace.zip"
        context = _mission_context(mission)
        loop: ActionLoop | None = None
        observation = None

        try:
            self._oracle.reset()
            self._guardrails.reset()
            await self._browser.start_browser(self._settings)
            own_context = not self._browser.is_context_active
            if own_context:
                await self._browser.create_context(self._settings)
            await self._browser.navigate(mission.target_url)
            loop = ActionLoop(
                llm=self._llm,
                browser=self._browser,
                perception=self._perception,
                guardrails=self._guardrails,
                context_mgr=ContextManager(
                    window_size=self._settings.context_window_size,
                    llm=self._llm,
                ),
                artifact_store=self._artifact_store,
                settings=self._settings,
                session_id=session_id,
                mission_id=mission.mission_id,
            )

            stopped_early = False
            for step_index, action_step in enumerate(mission.actions):
                if step_index >= self._settings.max_actions_per_mission:
                    stopped_early = True
                    verdict_reasoning = (
                        f"Mission exceeded the configured action limit of "
                        f"{self._settings.max_actions_per_mission}."
                    )
                    break
                step_result = await loop.run_guided_step(action_step, context, step_index)
                steps.append(step_result)
                replay = loop.last_replay_step
                if replay is not None:
                    replay_steps.append(replay)
                observation = loop.last_observation
                if step_result.error or step_result.was_blocked or observation is None:
                    stopped_early = True
                    verdict_reasoning = (
                        f"Guided action {action_step.step_id!r} did not complete: "
                        f"{step_result.error or step_result.block_reason or 'blocked'}."
                    )
                    break
                if action_step.checkpoint is not None:
                    checkpoint = await self._oracle.evaluate_checkpoint(
                        action_step.checkpoint,
                        observation,
                        loop.get_step_history(),
                    )
                    checkpoint_results.append(checkpoint)
                    invariant_violations.extend(checkpoint.invariant_violations)
                    if (
                        not checkpoint.passed
                        and checkpoint.required
                        and not action_step.checkpoint.continue_on_failure
                    ):
                        stopped_early = True
                        verdict_reasoning = (
                            f"Required checkpoint failed after action {action_step.step_id!r}."
                        )
                        break

            if observation is None:
                observation = await self._perception.capture(
                    self._browser,
                    self._artifact_store,
                    session_id,
                    mission.mission_id,
                    len(steps),
                )
            all_steps = loop.get_step_history() if loop is not None else steps
            final_evaluations = await self._oracle.evaluate_final(
                mission.expected_behaviors,
                observation,
                all_steps,
            )
            invariant_violations.extend(
                await self._oracle.check_invariants(mission.invariants, observation)
            )
            anomalies = await self._oracle.detect_anomalies(
                observation,
                all_steps,
                expected_url=mission.target_url,
            )

            if own_context and self._browser.is_context_active:
                await self._browser.close_context(trace_path)
            investigator = self._investigator
            if anomalies and investigator is None and loop is not None and self._investigator_factory is not None:
                investigator = self._investigator_factory(loop)
            if investigator is not None and loop is not None:
                for anomaly in anomalies:
                    try:
                        bug = await investigator.investigate(
                            anomaly,
                            replay_steps,
                            mission.target_url,
                        )
                    except Exception:  # noqa: BLE001  # Investigation failure remains an unresolved anomaly.
                        bug = None
                    if bug is not None:
                        bugs.append(bug)

            status = self._determine_status(
                checkpoint_results,
                final_evaluations,
                bugs,
                invariant_violations,
                stopped_early=stopped_early,
            )
            if status == "passed":
                if bugs:
                    bug_titles = "; ".join(bug.title for bug in bugs)
                    verdict_reasoning = (
                        f"All required guided checks passed, but {len(bugs)} confirmed "
                        f"bug(s) were detected: {bug_titles}."
                    )
                else:
                    verdict_reasoning = "All required guided checks passed with no confirmed bugs."
            elif status == "uncertain":
                verdict_reasoning = "At least one required behavior could not be verified confidently."
            elif not verdict_reasoning or verdict_reasoning == "Mission did not complete.":
                verdict_reasoning = "One or more required behaviors or invariants failed."
        except Exception as exc:  # noqa: BLE001  # Mission failures become machine-readable results.
            status = "error"
            verdict_reasoning = f"Mission execution error: {type(exc).__name__}: {exc}"
        finally:
            if own_context and self._browser.is_context_active:
                try:
                    await self._browser.close_context(trace_path)
                except Exception as exc:  # noqa: BLE001  # Preserve the primary mission outcome.
                    verdict_reasoning += f" Trace persistence failed: {type(exc).__name__}: {exc}"

        all_evidence = _collect_evidence(
            steps,
            checkpoint_results,
            final_evaluations,
            self._oracle.get_evidence(),
            bugs,
        )
        artifacts = ArtifactPaths(
            trace_path=self._relative(trace_path) if trace_path.is_file() else None,
            screenshots_dir=self._relative(
                self._artifact_store.get_mission_dir(session_id, mission.mission_id) / "steps"
            ),
            report_path=self._relative(
                self._artifact_store.get_mission_dir(session_id, mission.mission_id)
                / "mission_result.json"
            ),
        )
        mission_result = MissionResult(
            mission_id=mission.mission_id,
            goal=mission.goal,
            status=status,
            verdict_reasoning=verdict_reasoning,
            steps=steps,
            checkpoint_results=checkpoint_results,
            final_evaluations=final_evaluations,
            invariant_violations=_unique_strings(invariant_violations),
            anomalies=_unique_strings(anomalies),
            bugs=bugs,
            all_evidence=all_evidence,
            duration_ms=round((time.perf_counter() - started) * 1000),
            token_usage=_token_usage(self._llm),
            artifacts=artifacts,
        )
        await self._artifact_store.save_mission_result(session_id, mission_result)
        return mission_result

    @staticmethod
    def _determine_status(
        checkpoint_results: list[CheckpointResult],
        final_evaluations: list[ExpectationEvaluation],
        bugs: list[BugReport],
        invariant_violations: list[str],
        stopped_early: bool = False,
    ) -> str:
        if any(item.required and not item.passed for item in checkpoint_results):
            return "failed"
        if any(item.severity in {"critical", "major"} for item in bugs):
            return "failed"
        if invariant_violations:
            return "failed"
        if any(item.verdict == "fail" and item.severity == "must" for item in final_evaluations):
            return "failed"
        if any(item.verdict == "uncertain" and item.severity == "must" for item in final_evaluations):
            return "uncertain"
        if stopped_early:
            return "failed"
        if bugs:
            return "passed_with_bugs"
        return "passed"

    def _relative(self, path: Path) -> str:
        return path.relative_to(self._artifact_store.base_dir).as_posix()


def _mission_context(mission: TestMission) -> MissionContext:
    return MissionContext(
        mission_id=mission.mission_id,
        goal=mission.goal,
        target_url=mission.target_url,
        expected_behaviors=mission.expected_behaviors,
        invariants=mission.invariants,
        edge_cases=mission.edge_cases,
        tags=mission.tags,
    )


def _collect_evidence(
    steps: list[StepResult],
    checkpoints: list[CheckpointResult],
    evaluations: list[ExpectationEvaluation],
    oracle_evidence: list[Evidence],
    bugs: list[BugReport],
)-> list[Evidence]:
    values = [item for step in steps for item in step.evidence]
    values.extend(item for checkpoint in checkpoints for item in checkpoint.evidence)
    values.extend(oracle_evidence)
    values.extend(item for bug in bugs for item in bug.evidence)
    del evaluations  # Evaluation references are already represented in oracle_evidence.
    return _unique_evidence(values)


def _unique_evidence(values: list[Evidence]) -> list[Evidence]:
    seen: set[str] = set()
    unique: list[Evidence] = []
    for value in values:
        if value.evidence_id not in seen:
            seen.add(value.evidence_id)
            unique.append(value)
    return unique


def _unique_strings(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _token_usage(llm: LLMAdapter) -> TokenUsage:
    usage = getattr(llm, "token_usage", None)
    return usage if isinstance(usage, TokenUsage) else TokenUsage()
