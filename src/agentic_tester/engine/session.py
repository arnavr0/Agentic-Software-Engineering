"""Session-level orchestration for isolated guided browser missions."""

from __future__ import annotations

import asyncio
import time
import traceback
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from agentic_tester.agent.guardrails import GuardrailsEngine
from agentic_tester.agent.investigator import BugInvestigator
from agentic_tester.artifacts.store import ArtifactStore
from agentic_tester.browser.perception import PerceptionEngine
from agentic_tester.browser.worker import BrowserWorker
from agentic_tester.config import Settings, apply_settings_overrides
from agentic_tester.engine.mission_executor import MissionExecutor
from agentic_tester.engine.oracle import OracleEngine
from agentic_tester.llm.base import LLMAdapter
from agentic_tester.llm.factory import create_llm_adapter
from agentic_tester.logging import configure_logging, get_logger
from agentic_tester.models.mission import TestMission
from agentic_tester.models.results import (
    ArtifactPaths,
    MissionResult,
    SessionReport,
    TokenUsage,
)
from agentic_tester.models.session import TestSession

_PRIORITY = {"critical": 0, "high": 1, "medium": 2, "low": 3}
_LOGGER = get_logger(__name__)


class SessionEngine:
    """Own one browser process and execute a session's missions sequentially."""

    def __init__(
        self,
        settings: Settings,
        *,
        browser: BrowserWorker | None = None,
        llm: LLMAdapter | None = None,
        llm_factory: Callable[[Settings], LLMAdapter] | None = None,
        perception_factory: Callable[[], PerceptionEngine] = PerceptionEngine,
    ) -> None:
        self._settings = settings
        self._browser = browser or BrowserWorker()
        self._llm = llm
        self._llm_factory = llm_factory or create_llm_adapter
        self._perception_factory = perception_factory

    @property
    def browser(self) -> BrowserWorker:
        """Expose the worker for diagnostics and controlled shutdowns."""

        return self._browser

    async def run(self, session: TestSession) -> SessionReport:
        """Execute a session and persist its complete report.

        A mission result is deliberately isolated from its neighbors: every
        mission receives a new browser context, a new guardrail engine, and a
        new Oracle engine.  The LLM adapter and browser process are reused.
        """

        if not session.missions:
            raise ValueError("a test session must contain at least one mission")

        configure_logging(self._settings.log_level)
        effective_settings = apply_settings_overrides(
            self._settings,
            session.config_overrides,
        )
        artifact_store = ArtifactStore(effective_settings.artifact_dir)
        started = time.perf_counter()
        session.started_at = datetime.now(UTC)
        session.status = "running"
        ordered_missions = sorted(
            enumerate(session.missions),
            key=lambda item: (_PRIORITY[item[1].priority], item[0]),
        )
        mission_results: list[MissionResult] = []
        session_error: str | None = None
        llm: LLMAdapter | None = self._llm
        session_usage_before: TokenUsage | None = None

        _LOGGER.info(
            "session_started",
            session_id=session.session_id,
            mission_count=len(ordered_missions),
        )
        try:
            if llm is None:
                llm = self._llm_factory(effective_settings)
            session_usage_before = _usage_snapshot(llm)
            await self._browser.start_browser(effective_settings)

            for _, mission in ordered_missions:
                if not _browser_is_healthy(self._browser):
                    message = "Skipped because the session browser process is not healthy."
                    result = _error_result(
                        mission,
                        artifact_store,
                        session.session_id,
                        message,
                    )
                    mission_results.append(result)
                    _LOGGER.error(
                        "mission_skipped_browser_unhealthy",
                        session_id=session.session_id,
                        mission_id=mission.mission_id,
                    )
                    continue

                result = await self._run_mission(
                    session,
                    mission,
                    effective_settings,
                    artifact_store,
                    llm,
                )
                mission_results.append(result)

            summary = await self._generate_summary(llm, mission_results)
        except asyncio.CancelledError:
            session.status = "cancelled"
            _LOGGER.warning("session_cancelled", session_id=session.session_id)
            raise
        except Exception as exc:  # noqa: BLE001  # Convert infrastructure errors to a report.
            session_error = f"{type(exc).__name__}: {exc}"
            summary = f"Session could not complete: {session_error}"
            tb = traceback.format_exc()
            _LOGGER.error(
                "session_failed",
                session_id=session.session_id,
                error_type=type(exc).__name__,
                traceback=tb,
            )
        finally:
            try:
                await self._browser.stop_browser()
            except Exception as exc:  # noqa: BLE001  # Preserve the primary report.
                stop_error = f"{type(exc).__name__}: {exc}"
                session_error = session_error or f"Browser shutdown failed: {stop_error}"
                _LOGGER.error(
                    "browser_shutdown_failed",
                    session_id=session.session_id,
                    error_type=type(exc).__name__,
                )

        completed_at = datetime.now(UTC)
        session.completed_at = completed_at
        overall_status = _overall_status(mission_results, session_error)
        total_usage = _usage_delta(session_usage_before, _usage_snapshot(llm))
        if total_usage is None:
            total_usage = _sum_usage(result.token_usage for result in mission_results)
        report = SessionReport(
            session_id=session.session_id,
            target_base_url=session.target_base_url,
            overall_status=overall_status,
            summary=summary,
            mission_results=mission_results,
            total_bugs=sum(len(result.bugs) for result in mission_results),
            total_duration_ms=round((time.perf_counter() - started) * 1000),
            total_token_usage=total_usage,
            artifact_root=str(artifact_store.base_dir.resolve()),
            completed_at=completed_at,
        )
        await artifact_store.save_session_report(report)
        session.status = "failed" if overall_status == "error" else "completed"
        _LOGGER.info(
            "session_completed",
            session_id=session.session_id,
            overall_status=overall_status,
            mission_count=len(mission_results),
            total_bugs=report.total_bugs,
            total_duration_ms=report.total_duration_ms,
            total_tokens=report.total_token_usage.total_tokens,
        )
        return report

    async def _run_mission(
        self,
        session: TestSession,
        mission: TestMission,
        settings: Settings,
        artifact_store: ArtifactStore,
        llm: LLMAdapter,
    ) -> MissionResult:
        """Run one isolated mission and always dispose its active context."""

        trace_path = artifact_store.get_mission_dir(
            session.session_id,
            mission.mission_id,
        ) / "trace.zip"
        usage_before = _usage_snapshot(llm)
        result: MissionResult | None = None
        context_error: str | None = None

        with _session_log_context(session.session_id, mission.mission_id):
            _LOGGER.info("mission_started", priority=mission.priority)
            try:
                await self._browser.create_context(settings)
                executor = MissionExecutor(
                    llm=llm,
                    browser=self._browser,
                    perception=self._perception_factory(),
                    oracle=OracleEngine(llm, artifact_store),
                    investigator=None,
                    investigator_factory=lambda loop: BugInvestigator(
                        loop,
                        llm,
                        self._browser,
                        settings,
                    ),
                    guardrails=GuardrailsEngine(session.execution_policy),
                    artifact_store=artifact_store,
                    settings=settings,
                )
                result = await executor.execute(mission, session.session_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001  # Isolate a mission failure.
                context_error = f"{type(exc).__name__}: {exc}"
                tb = traceback.format_exc()
                _LOGGER.error(
                    "mission_execution_failed",
                    error_type=type(exc).__name__,
                    traceback=tb,
                )
            finally:
                if self._browser.is_context_active:
                    try:
                        await self._browser.close_context(trace_path)
                    except Exception as exc:  # noqa: BLE001  # Report trace failures explicitly.
                        close_error = f"{type(exc).__name__}: {exc}"
                        context_error = context_error or f"Trace persistence failed: {close_error}"
                        _LOGGER.error(
                            "mission_context_close_failed",
                            error_type=type(exc).__name__,
                        )

        if result is None:
            result = _error_result(
                mission,
                artifact_store,
                session.session_id,
                context_error or "Mission did not produce a result.",
            )
        elif context_error is not None:
            result = result.model_copy(
                update={
                    "status": "error",
                    "verdict_reasoning": f"{result.verdict_reasoning} {context_error}",
                }
            )
        if result is not None and trace_path.is_file() and result.artifacts.trace_path is None:
            result = result.model_copy(
                update={
                    "artifacts": result.artifacts.model_copy(
                        update={"trace_path": _relative(artifact_store.base_dir, trace_path)}
                    )
                }
            )

        usage_after = _usage_snapshot(llm)
        mission_usage = _usage_delta(usage_before, usage_after)
        if mission_usage is not None:
            result = result.model_copy(update={"token_usage": mission_usage})
        await artifact_store.save_mission_result(session.session_id, result)
        _LOGGER.info(
            "mission_completed",
            mission_id=mission.mission_id,
            status=result.status,
            step_count=len(result.steps),
            bug_count=len(result.bugs),
            total_tokens=result.token_usage.total_tokens,
        )
        return result

    async def _generate_summary(
        self,
        llm: LLMAdapter,
        mission_results: list[MissionResult],
    ) -> str:
        try:
            return await llm.generate_session_summary(mission_results)
        except Exception as exc:  # noqa: BLE001  # A report remains useful without prose synthesis.
            _LOGGER.warning(
                "session_summary_failed",
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return _fallback_summary(mission_results)


def _browser_is_healthy(browser: BrowserWorker) -> bool:
    value = getattr(browser, "is_browser_healthy", True)
    return bool(value() if callable(value) else value)


def _overall_status(
    mission_results: list[MissionResult],
    session_error: str | None,
) -> str:
    if session_error is not None:
        if not mission_results or all(result.status == "error" for result in mission_results):
            return "error"
        return "mixed"
    if not mission_results:
        return "error"
    statuses = {result.status for result in mission_results}
    if statuses == {"passed"}:
        return "passed"
    if statuses == {"failed"}:
        return "failed"
    if statuses == {"error"}:
        return "error"
    return "mixed"


def _fallback_summary(results: list[MissionResult]) -> str:
    counts: dict[str, int] = {}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    if not counts:
        return "No missions completed."
    details = ", ".join(f"{count} {status}" for status, count in sorted(counts.items()))
    lines = [f"Session completed with {details} mission result(s)."]
    for result in results:
        for bug in result.bugs:
            lines.append(
                f"[{bug.severity}] {bug.title} ({result.mission_id}): "
                f"{bug.actual_behavior} Reproduction: {bug.reproduction_success_rate}."
            )
    return " ".join(lines)


def _error_result(
    mission: TestMission,
    artifact_store: ArtifactStore,
    session_id: str,
    reason: str,
) -> MissionResult:
    mission_dir = artifact_store.get_mission_dir(session_id, mission.mission_id)
    return MissionResult(
        mission_id=mission.mission_id,
        goal=mission.goal,
        status="error",
        verdict_reasoning=reason,
        duration_ms=0,
        artifacts=ArtifactPaths(
            screenshots_dir=_relative(artifact_store.base_dir, mission_dir / "steps"),
            report_path=_relative(artifact_store.base_dir, mission_dir / "mission_result.json"),
        ),
    )


def _usage_snapshot(llm: LLMAdapter | None) -> TokenUsage | None:
    usage = getattr(llm, "token_usage", None) if llm is not None else None
    if isinstance(usage, TokenUsage):
        return usage.model_copy(deep=True)
    return None


def _usage_delta(
    before: TokenUsage | None,
    after: TokenUsage | None,
) -> TokenUsage | None:
    if before is None or after is None:
        return None
    return TokenUsage(
        input_tokens=max(0, after.input_tokens - before.input_tokens),
        output_tokens=max(0, after.output_tokens - before.output_tokens),
        total_tokens=max(0, after.total_tokens - before.total_tokens),
    )


def _sum_usage(values: Iterator[TokenUsage]) -> TokenUsage:
    input_tokens = output_tokens = total_tokens = 0
    for value in values:
        input_tokens += value.input_tokens
        output_tokens += value.output_tokens
        total_tokens += value.total_tokens
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
    )


def _relative(base_dir: Path, path: Path) -> str:
    return path.relative_to(base_dir).as_posix()


@contextmanager
def _session_log_context(session_id: str, mission_id: str) -> Iterator[None]:
    """Bind stable identifiers to every mission log event."""

    import structlog

    with structlog.contextvars.bound_contextvars(
        session_id=session_id,
        mission_id=mission_id,
    ):
        yield
