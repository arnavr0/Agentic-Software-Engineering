"""Checkpoint-ready perceive/plan/act/observe loop with evidence collection."""

import time
from datetime import UTC, datetime

from agentic_tester.agent.action_space import ActionDecision, ActionType
from agentic_tester.agent.context_manager import ContextManager
from agentic_tester.agent.guardrails import GuardrailsEngine
from agentic_tester.artifacts.store import ArtifactStore
from agentic_tester.browser.perception import PerceptionEngine
from agentic_tester.browser.worker import BrowserWorker
from agentic_tester.config import Settings
from agentic_tester.llm.base import LLMAdapter
from agentic_tester.llm.schemas import MissionContext
from agentic_tester.models.element import ElementRef
from agentic_tester.models.evidence import Evidence, EvidenceType
from agentic_tester.models.mission import ActionStep
from agentic_tester.models.observation import PageObservation
from agentic_tester.models.results import ReplayStep, StepResult


class ActionLoop:
    """Execute one action at a time while preserving evidence and replayability."""

    def __init__(
        self,
        llm: LLMAdapter,
        browser: BrowserWorker,
        perception: PerceptionEngine,
        guardrails: GuardrailsEngine,
        context_mgr: ContextManager,
        artifact_store: ArtifactStore,
        settings: Settings,
        session_id: str = "session",
        mission_id: str | None = None,
    ) -> None:
        self._llm = llm
        self._browser = browser
        self._perception = perception
        self._guardrails = guardrails
        self._context_mgr = context_mgr
        self._artifact_store = artifact_store
        self._settings = settings
        self._session_id = session_id
        self._mission_id = mission_id
        self._active_context: MissionContext | None = None
        self._active_step_index = 0
        self._last_observation: PageObservation | None = None
        self._last_step_result: StepResult | None = None
        self._last_replay_step: ReplayStep | None = None

    @property
    def last_observation(self) -> PageObservation | None:
        """Return the most recent browser observation produced by this loop."""

        return self._last_observation

    @property
    def last_step_result(self) -> StepResult | None:
        """Return the most recent completed step result."""

        return self._last_step_result

    @property
    def last_replay_step(self) -> ReplayStep | None:
        """Return a deterministic replay record for the most recent browser action."""

        return self._last_replay_step.model_copy(deep=True) if self._last_replay_step else None

    def get_step_history(self) -> list[StepResult]:
        """Return the complete step history accumulated by this loop."""

        return self._context_mgr.get_full_history()

    def set_replay_context(self, mission_context: MissionContext) -> None:
        """Set the mission contract used by deterministic replay steps."""

        self._active_context = mission_context

    async def capture_observation(
        self,
        mission_context: MissionContext,
        step_index: int,
    ) -> PageObservation:
        """Capture a replay-start observation without adding a step result."""

        self._set_active_context(mission_context, step_index)
        return await self._capture(mission_context, step_index)

    async def run_guided_step(
        self,
        step: ActionStep,
        mission_context: MissionContext,
        step_index: int,
    ) -> StepResult:
        """Plan and execute one mission action, asking for safe alternatives if needed."""

        started = time.perf_counter()
        self._last_replay_step = None
        before = await self._capture(mission_context, step_index)
        self._set_active_context(mission_context, step_index)
        summary, recent_steps = await self._context_mgr.get_context()
        guided_description = step.description
        if step.selector_hint:
            guided_description += f"\nSelector hint: {step.selector_hint}"
        if step.input_data:
            guided_description += f"\nInput data: {step.input_data}"
        current_context = mission_context.model_copy(
            update={"current_step_description": guided_description}
        )
        try:
            decision = await self._llm.plan_next_action(
                before,
                current_context,
                summary,
                recent_steps,
            )
        except Exception as exc:  # noqa: BLE001  # Planning failures become structured step errors.
            return await self._finish(
                self._result(
                    step_index=step_index,
                    step_id=step.step_id,
                    decision=None,
                    before=before,
                    after=before,
                    started=started,
                    error=f"LLM planning failed: {type(exc).__name__}: {exc}",
                    evidence=self._observation_evidence(before, step_index, "perception"),
                )
            )

        return await self._run_decision(
            decision=decision,
            step_id=step.step_id,
            description=step.description,
            mission_context=mission_context,
            step_index=step_index,
            before=before,
            started=started,
            history_summary=summary,
            recent_steps=recent_steps,
            allow_alternatives=True,
        )

    async def replay_step(self, replay: ReplayStep, step_index: int) -> StepResult:
        """Replay one recorded action without consulting the LLM."""

        started = time.perf_counter()
        self._last_replay_step = None
        context = self._active_context or MissionContext(
            mission_id=self._mission_id or "mission",
            goal="Replay a recorded action",
            target_url=await self._browser.get_page_url(),
        )
        before = await self._capture(context, step_index)
        self._set_active_context(context, step_index)
        if (
            replay.expected_state_fingerprint is not None
            and replay.expected_state_fingerprint != before.state_fingerprint
        ):
            return await self._finish(
                self._result(
                    step_index=step_index,
                    step_id=None,
                    decision=None,
                    before=before,
                    after=before,
                    started=started,
                    error=(
                        "replay state fingerprint mismatch: "
                        f"expected {replay.expected_state_fingerprint}, got {before.state_fingerprint}"
                    ),
                    evidence=self._observation_evidence(before, step_index, "replay"),
                )
            )
        try:
            action_type = ActionType(replay.action_type)
        except ValueError:
            return await self._finish(
                self._result(
                    step_index=step_index,
                    step_id=None,
                    decision=None,
                    before=before,
                    after=before,
                    started=started,
                    error=f"invalid replay action type: {replay.action_type}",
                    evidence=self._observation_evidence(before, step_index, "replay"),
                )
            )

        decision = ActionDecision(
            action_type=action_type,
            ref=replay.element_ref.id if replay.element_ref is not None else None,
            value=replay.value,
            key=replay.key,
            reasoning="Deterministic replay step.",
            confidence=1.0,
            expected_result="Replay the recorded browser action.",
        )
        if replay.element_ref is not None and not any(
            element.id == replay.element_ref.id for element in before.interactive_elements
        ):
            before = before.model_copy(
                update={
                    "interactive_elements": [
                        *before.interactive_elements,
                        replay.element_ref,
                    ]
                }
            )
        return await self._run_decision(
            decision=decision,
            step_id=None,
            description="Replay recorded action",
            mission_context=context,
            step_index=step_index,
            before=before,
            started=started,
            history_summary="",
            recent_steps=[],
            allow_alternatives=False,
            include_context=False,
        )

    async def _execute_with_retry(
        self,
        action: ActionDecision,
        elements: list[ElementRef],
        step_description: str,
        max_retries: int = 3,
        mission_context: MissionContext | None = None,
        step_index: int | None = None,
    ) -> tuple[str | None, PageObservation]:
        """Execute and re-observe an action, retrying the same action if requested."""

        del step_description  # Reserved for provider-specific retry prompts in a later phase.
        active_context = mission_context or self._active_context
        if active_context is None:
            raise RuntimeError("an active mission context is required for action retries")
        active_step_index = self._active_step_index if step_index is None else step_index
        current_elements = list(elements)
        last_error: str | None = None
        for _ in range(max(1, max_retries)):
            last_error = await self._browser.execute_action(action, current_elements)
            after = await self._capture(active_context, active_step_index)
            if last_error is None:
                return None, after
            current_elements = after.interactive_elements
        return last_error, after

    async def _run_decision(
        self,
        decision: ActionDecision,
        step_id: str | None,
        description: str,
        mission_context: MissionContext,
        step_index: int,
        before: PageObservation,
        started: float,
        history_summary: str,
        recent_steps: list[StepResult],
        allow_alternatives: bool,
        include_context: bool = True,
    ) -> StepResult:
        current_decision = decision
        current_before = before
        max_attempts = max(1, self._settings.max_retries_per_action + 1)
        blocked_reasons: list[str] = []
        for attempt in range(max_attempts):
            element = _element_for(current_decision, current_before.interactive_elements)
            guardrail = self._guardrails.check(
                current_decision,
                current_before.url,
                element,
                mission_context.target_url,
            )
            if not guardrail.allowed:
                blocked_reasons.append(guardrail.reason or "blocked by execution policy")
                if not allow_alternatives or attempt + 1 >= max_attempts:
                    evidence = self._observation_evidence(before, step_index, "perception")
                    evidence.append(self._action_evidence(current_decision, step_index, blocked_reasons[-1]))
                    return await self._finish(
                        self._result(
                            step_index=step_index,
                            step_id=step_id,
                            decision=current_decision,
                            before=before,
                            after=current_before,
                            started=started,
                            was_blocked=True,
                            block_reason="; ".join(blocked_reasons),
                            evidence=evidence,
                        ),
                        include_context=include_context,
                    )
                try:
                    current_decision = await self._llm.plan_next_action(
                        current_before,
                        mission_context,
                        _blocked_history(history_summary, blocked_reasons),
                        recent_steps,
                    )
                except Exception as exc:  # noqa: BLE001  # Alternative planning is structured.
                    evidence = self._observation_evidence(before, step_index, "perception")
                    evidence.append(self._action_evidence(current_decision, step_index, str(exc)))
                    return await self._finish(
                        self._result(
                            step_index=step_index,
                            step_id=step_id,
                            decision=current_decision,
                            before=before,
                            after=current_before,
                            started=started,
                            error=f"alternative planning failed: {type(exc).__name__}: {exc}",
                            evidence=evidence,
                        ),
                        include_context=include_context,
                    )
                continue

            error, after = await self._execute_with_retry(
                current_decision,
                current_before.interactive_elements,
                description,
                max_retries=1,
                mission_context=mission_context,
                step_index=step_index,
            )
            if error is None:
                self._guardrails.record_success()
                self._remember_replay(current_decision, current_before)
                evidence = (
                    self._observation_evidence(before, step_index, "perception")
                    + [self._action_evidence(current_decision, step_index, description)]
                    + self._observation_evidence(after, step_index, "post_action")
                )
                return await self._finish(
                    self._result(
                        step_index=step_index,
                        step_id=step_id,
                        decision=current_decision,
                        before=before,
                        after=after,
                        started=started,
                        evidence=evidence,
                    ),
                    include_context=include_context,
                )

            self._guardrails.record_error()
            if not allow_alternatives or attempt + 1 >= max_attempts:
                self._remember_replay(current_decision, current_before)
                evidence = (
                    self._observation_evidence(before, step_index, "perception")
                    + [self._action_evidence(current_decision, step_index, error)]
                    + self._observation_evidence(after, step_index, "post_action")
                )
                return await self._finish(
                    self._result(
                        step_index=step_index,
                        step_id=step_id,
                        decision=current_decision,
                        before=before,
                        after=after,
                        started=started,
                        error=error,
                        evidence=evidence,
                    ),
                    include_context=include_context,
                )

            try:
                current_decision = await self._llm.plan_next_action(
                    after,
                    mission_context,
                    _retry_history(history_summary, error),
                    recent_steps,
                )
            except Exception as exc:  # noqa: BLE001  # Retry planning is structured.
                evidence = (
                    self._observation_evidence(before, step_index, "perception")
                    + [self._action_evidence(current_decision, step_index, error)]
                    + self._observation_evidence(after, step_index, "post_action")
                )
                return await self._finish(
                    self._result(
                        step_index=step_index,
                        step_id=step_id,
                        decision=current_decision,
                        before=before,
                        after=after,
                        started=started,
                        error=f"retry planning failed: {type(exc).__name__}: {exc}",
                        evidence=evidence,
                    ),
                    include_context=include_context,
                )
            current_before = after

        raise RuntimeError("action loop exhausted without producing a result")

    async def _capture(self, mission_context: MissionContext, step_index: int) -> PageObservation:
        mission_id = self._mission_id or mission_context.mission_id
        observation = await self._perception.capture(
            self._browser,
            self._artifact_store,
            self._session_id,
            mission_id,
            step_index,
        )
        self._last_observation = observation
        return observation

    def _set_active_context(self, context: MissionContext, step_index: int) -> None:
        self._active_context = context
        self._active_step_index = step_index

    async def _finish(self, result: StepResult, include_context: bool = True) -> StepResult:
        if include_context:
            self._context_mgr.add_step(result)
        self._last_step_result = result
        await self._artifact_store.save_step_result(
            self._session_id,
            self._current_mission_id(),
            result,
        )
        return result

    def _remember_replay(self, decision: ActionDecision, observation: PageObservation) -> None:
        if decision.action_type in {ActionType.DONE, ActionType.STUCK}:
            self._last_replay_step = None
            return
        self._last_replay_step = ReplayStep(
            action_type=decision.action_type.value,
            element_ref=_element_for(decision, observation.interactive_elements),
            value=decision.value,
            key=decision.key,
            expected_state_fingerprint=observation.state_fingerprint,
        )

    def _current_mission_id(self) -> str:
        if self._mission_id:
            return self._mission_id
        if self._active_context is not None:
            return self._active_context.mission_id
        return "mission"

    def _result(
        self,
        step_index: int,
        step_id: str | None,
        decision: ActionDecision | None,
        before: PageObservation,
        after: PageObservation,
        started: float,
        evidence: list[Evidence],
        error: str | None = None,
        was_blocked: bool = False,
        block_reason: str | None = None,
    ) -> StepResult:
        return StepResult(
            step_index=step_index,
            step_id=step_id,
            action_taken=_action_description(decision),
            action_type=decision.action_type.value if decision is not None else "none",
            reasoning=decision.reasoning if decision is not None else "No action was produced.",
            evidence=_deduplicate_evidence(evidence),
            page_url_before=before.url,
            page_url_after=after.url,
            duration_ms=round((time.perf_counter() - started) * 1000),
            timestamp=datetime.now(UTC),
            was_blocked=was_blocked,
            block_reason=block_reason,
            error=error,
        )

    def _observation_evidence(
        self,
        observation: PageObservation,
        step_index: int,
        source: str,
    ) -> list[Evidence]:
        evidence = [
            self._artifact_store.create_evidence(
                EvidenceType.SCREENSHOT,
                source,
                step_index,
                artifact_path=observation.screenshot_path,
                url=observation.url,
            ),
            self._artifact_store.create_evidence(
                EvidenceType.ACCESSIBILITY,
                source,
                step_index,
                excerpt=observation.accessibility_tree[:2_000],
                interactive_count=len(observation.interactive_elements),
            ),
            self._artifact_store.create_evidence(
                EvidenceType.URL,
                source,
                step_index,
                excerpt=observation.url,
                url=observation.url,
            ),
            self._artifact_store.create_evidence(
                EvidenceType.TEXT,
                source,
                step_index,
                excerpt=observation.visible_text_summary[:2_000],
            ),
        ]
        for console_entry in observation.console_entries:
            evidence.append(
                self._artifact_store.create_evidence(
                    EvidenceType.CONSOLE,
                    source,
                    step_index,
                    excerpt=console_entry.text,
                    level=console_entry.level,
                    timestamp=console_entry.timestamp.isoformat(),
                )
            )
        for network_entry in observation.network_log:
            evidence.append(
                self._artifact_store.create_evidence(
                    EvidenceType.NETWORK,
                    source,
                    step_index,
                    excerpt=f"{network_entry.method} {network_entry.url}",
                    **network_entry.model_dump(mode="json"),
                )
            )
        return evidence

    def _action_evidence(self, action: ActionDecision, step_index: int, detail: str) -> Evidence:
        return self._artifact_store.create_evidence(
            EvidenceType.ACTION,
            "action_loop",
            step_index,
            excerpt=detail,
            action_type=action.action_type.value,
            element_ref=action.ref,
            value=action.value,
            key=action.key,
        )


def _element_for(action: ActionDecision, elements: list[ElementRef]) -> ElementRef | None:
    if action.ref is None:
        return None
    return next((element for element in elements if element.id == action.ref), None)


def _action_description(action: ActionDecision | None) -> str:
    if action is None:
        return "No action"
    details = [action.action_type.value]
    if action.ref:
        details.append(action.ref)
    if action.value:
        details.append(repr(action.value))
    if action.key:
        details.append(f"key={action.key}")
    return " ".join(details)


def _blocked_history(summary: str, reasons: list[str]) -> str:
    return f"{summary}\nGuardrail blocked prior proposals: {'; '.join(reasons)}".strip()


def _retry_history(summary: str, error: str) -> str:
    return f"{summary}\nPrevious action error: {error}".strip()


def _deduplicate_evidence(evidence: list[Evidence]) -> list[Evidence]:
    seen: set[str] = set()
    result: list[Evidence] = []
    for item in evidence:
        if item.evidence_id not in seen:
            result.append(item)
            seen.add(item.evidence_id)
    return result
