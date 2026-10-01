"""Reproduction and minimization of anomalies found during guided missions."""

from uuid import uuid4

from agentic_tester.agent.action_loop import ActionLoop
from agentic_tester.browser.worker import BrowserWorker
from agentic_tester.config import Settings
from agentic_tester.llm.base import LLMAdapter
from agentic_tester.llm.projections import history_projection
from agentic_tester.llm.schemas import AnomalyAssessment, MissionContext
from agentic_tester.models.evidence import Evidence
from agentic_tester.models.observation import PageObservation
from agentic_tester.models.results import BugReport, ReplayStep


class BugInvestigator:
    """Confirm, minimize, and report anomalies using deterministic replays."""

    def __init__(
        self,
        action_loop: ActionLoop,
        llm: LLMAdapter,
        browser: BrowserWorker,
        settings: Settings,
    ) -> None:
        self._action_loop = action_loop
        self._llm = llm
        self._browser = browser
        self._settings = settings
        self._active_anomaly = ""

    async def investigate(
        self,
        anomaly: str,
        replay_steps: list[ReplayStep],
        target_url: str,
        max_reproduce_attempts: int = 3,
    ) -> BugReport | None:
        """Return a bug report only when the anomaly reproduces at least twice."""

        self._active_anomaly = anomaly
        attempts = max(2, max_reproduce_attempts)
        successes = 0
        evidence: list[Evidence] = []
        latest_evidence: list[Evidence] = []
        for attempt in range(attempts):
            reproduced, attempt_evidence = await self._run_attempt(
                anomaly,
                replay_steps,
                target_url,
                attempt,
            )
            evidence.extend(attempt_evidence)
            latest_evidence = list(attempt_evidence)
            if reproduced:
                successes += 1
        if successes < 2:
            return None

        minimized = list(replay_steps)
        budget = self._settings.max_minimization_attempts
        checks = 0
        index = 0
        while index < len(minimized) and checks < budget:
            candidate = minimized[:index] + minimized[index + 1 :]
            reproduced, attempt_evidence = await self._run_attempt(
                anomaly,
                candidate,
                target_url,
                attempts + checks,
            )
            checks += 1
            evidence.extend(attempt_evidence)
            if reproduced:
                minimized = candidate
            else:
                index += 1

        observation = self._action_loop.last_observation
        if observation is None:
            return None
        relevant_evidence = _unique_evidence(latest_evidence or evidence)
        assessment = await self._assess(anomaly, observation, relevant_evidence)
        if assessment.classification == "not_anomaly":
            return None
        readable_steps = await self._readable_steps(anomaly)
        return BugReport(
            bug_id=str(uuid4()),
            title=assessment.title or _short_title(anomaly),
            severity=_severity(assessment),
            description=assessment.description or anomaly,
            expected_behavior=assessment.expected_behavior
            or "The explicit mission contract should remain satisfied.",
            actual_behavior=assessment.actual_behavior or anomaly,
            reproduction_steps=minimized,
            reproduction_steps_readable=readable_steps,
            reproduction_success_rate=f"{successes}/{attempts}",
            evidence=relevant_evidence,
            confidence=min(1.0, max(0.0, assessment.confidence)),
        )

    async def _run_attempt(
        self,
        anomaly: str,
        replay_steps: list[ReplayStep],
        target_url: str,
        attempt_index: int,
    ) -> tuple[bool, list[Evidence]]:
        had_context = self._browser.is_context_active
        if not had_context:
            await self._browser.create_context(self._settings)
        evidence: list[Evidence] = []
        try:
            await self._browser.navigate(target_url)
            base_index = 10_000 + attempt_index * (max(1, len(replay_steps)) + 1)
            context = MissionContext(
                mission_id="investigation",
                goal="Reproduce a reported mission anomaly",
                target_url=target_url,
            )
            self._action_loop.set_replay_context(context)
            await self._action_loop.capture_observation(
                context,
                step_index=base_index,
            )
            for offset, replay in enumerate(replay_steps):
                result = await self._action_loop.replay_step(
                    replay,
                    step_index=base_index + 1 + offset,
                )
                evidence.extend(result.evidence)
                if result.error:
                    return False, evidence
            observation = self._action_loop.last_observation
            return observation is not None and _anomaly_present(anomaly, observation), evidence
        finally:
            if not had_context and self._browser.is_context_active:
                trace_path = (
                    self._settings.artifact_dir
                    / "investigations"
                    / f"replay-{attempt_index}-{uuid4().hex[:8]}.zip"
                )
                await self._browser.close_context(trace_path)

    async def _reproduce(self, steps: list[ReplayStep], target_url: str) -> bool:
        """Replay the current anomaly contract once and return whether it recurs."""

        reproduced, _ = await self._run_attempt(
            self._active_anomaly,
            steps,
            target_url,
            0,
        )
        return reproduced

    async def _minimize(self, steps: list[ReplayStep], target_url: str) -> list[ReplayStep]:
        """Greedily remove steps while the current anomaly continues to reproduce."""

        minimized = list(steps)
        checks = 0
        index = 0
        while index < len(minimized) and checks < self._settings.max_minimization_attempts:
            candidate = minimized[:index] + minimized[index + 1 :]
            checks += 1
            if await self._reproduce(candidate, target_url):
                minimized = candidate
            else:
                index += 1
        return minimized

    def _steps_to_readable(self, steps: list[ReplayStep]) -> list[str]:
        """Convert replay records to concise deterministic descriptions."""

        return [
            " ".join(
                part
                for part in (
                    step.action_type,
                    step.element_ref.name if step.element_ref is not None else None,
                    repr(step.value) if step.value is not None else None,
                    f"key={step.key}" if step.key is not None else None,
                )
                if part
            )
            for step in steps
        ]

    async def _assess(
        self,
        anomaly: str,
        observation: PageObservation,
        evidence: list[Evidence],
    ) -> AnomalyAssessment:
        try:
            return await self._llm.assess_anomaly(anomaly, observation, evidence)
        except Exception:  # noqa: BLE001  # Deterministic fallback preserves confirmed evidence.
            return AnomalyAssessment(
                classification="bug",
                title=_short_title(anomaly),
                description=anomaly,
                actual_behavior=anomaly,
                confidence=0.8,
            )

    async def _readable_steps(self, anomaly: str) -> list[str]:
        readable: list[str] = []
        try:
            readable = await self._llm.extract_reproduction_steps(
                anomaly,
                history_projection(self._action_loop.get_step_history()),
            )
            if readable:
                return readable
        except Exception:  # noqa: BLE001  # Use deterministic fallback below.
            readable = []
        return [step.action_taken for step in self._action_loop.get_step_history()[-10:]]


def _anomaly_present(anomaly: str, observation: PageObservation) -> bool:
    normalized = anomaly.casefold()
    if "console error" in normalized:
        return any(entry.level == "error" for entry in observation.console_entries)
    if "http/network failure" in normalized or "network failure" in normalized:
        return any(
            entry.is_failure or (entry.status is not None and entry.status >= 400)
            for entry in observation.network_log
        )
    terms = [term for term in normalized.split() if len(term) > 4]
    haystack = f"{observation.visible_text_summary} {observation.accessibility_tree}".casefold()
    return bool(terms) and all(term in haystack for term in terms[:3])


def _severity(assessment: AnomalyAssessment) -> str:
    if assessment.severity == "info":
        return "major"
    return assessment.severity


def _short_title(anomaly: str) -> str:
    first_line = anomaly.splitlines()[0].strip()
    return first_line[:120] or "Reproduced application anomaly"


def _unique_evidence(values: list[Evidence]) -> list[Evidence]:
    seen: set[str] = set()
    unique: list[Evidence] = []
    for value in values:
        if value.evidence_id not in seen:
            seen.add(value.evidence_id)
            unique.append(value)
    return unique
