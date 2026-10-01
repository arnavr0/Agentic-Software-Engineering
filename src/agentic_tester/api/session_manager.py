"""In-memory lifecycle manager used by the POC HTTP API."""

from __future__ import annotations

import asyncio
import traceback
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from agentic_tester.artifacts.store import ArtifactStore
from agentic_tester.config import Settings, apply_settings_overrides
from agentic_tester.engine.session import SessionEngine
from agentic_tester.logging import get_logger
from agentic_tester.models.results import SessionReport
from agentic_tester.models.session import TestSession

_LOGGER = get_logger(__name__)


class SessionManager:
    """Track sessions and their background tasks in process memory."""

    def __init__(
        self,
        settings: Settings,
        engine_factory: Callable[[Settings], SessionEngine] | None = None,
    ) -> None:
        self._settings = settings
        self._engine_factory = engine_factory or (lambda runtime: SessionEngine(runtime))
        self._sessions: dict[str, TestSession] = {}
        self._reports: dict[str, SessionReport] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._errors: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def create_session(self, session: TestSession) -> TestSession:
        """Register a session and schedule its execution exactly once."""

        if not session.missions:
            raise ValueError("a test session must contain at least one mission")
        apply_settings_overrides(self._settings, session.config_overrides)
        async with self._lock:
            if session.session_id in self._sessions:
                raise ValueError(f"session already exists: {session.session_id}")
            self._sessions[session.session_id] = session
            task = asyncio.create_task(
                self._run_session(session.session_id),
                name=f"tester-session-{session.session_id}",
            )
            self._tasks[session.session_id] = task
        return session

    def get_session(self, session_id: str) -> TestSession | None:
        return self._sessions.get(session_id)

    def get_report(self, session_id: str) -> SessionReport | None:
        return self._reports.get(session_id)

    def get_error(self, session_id: str) -> str | None:
        return self._errors.get(session_id)

    async def cancel_session(self, session_id: str) -> TestSession | None:
        """Cancel a pending/running task; completed sessions remain readable."""

        session = self._sessions.get(session_id)
        if session is None:
            return None
        task = self._tasks.get(session_id)
        if task is not None and not task.done() and session.status in {"pending", "running"}:
            session.status = "cancelled"
            task.cancel()
            _LOGGER.info("session_cancel_requested", session_id=session_id)
        return session

    def artifact_path(self, session_id: str, requested_path: str) -> Path | None:
        """Resolve a session artifact while enforcing root and session containment."""

        session = self._sessions.get(session_id)
        if session is None or not requested_path:
            return None
        try:
            settings = apply_settings_overrides(self._settings, session.config_overrides)
        except (TypeError, ValueError):
            return None
        store = ArtifactStore(settings.artifact_dir)
        base = store.base_dir.resolve()
        session_dir = store.get_session_dir(session_id).resolve()
        relative = Path(requested_path)
        if relative.is_absolute() or "\x00" in requested_path:
            return None
        candidate = (base / relative).resolve()
        if not candidate.is_relative_to(session_dir):
            candidate = (session_dir / relative).resolve()
        if not candidate.is_relative_to(session_dir) or not candidate.is_file():
            return None
        return candidate

    async def _run_session(self, session_id: str) -> None:
        session = self._sessions[session_id]
        session.status = "running"
        session.started_at = session.started_at or datetime.now(UTC)
        try:
            runtime = apply_settings_overrides(self._settings, session.config_overrides)
            engine = self._engine_factory(runtime)
            report = await engine.run(session)
            self._reports[session_id] = report
            session.completed_at = session.completed_at or report.completed_at
            session.status = "failed" if report.overall_status == "error" else "completed"
        except asyncio.CancelledError:
            session.status = "cancelled"
            _LOGGER.info("session_cancelled", session_id=session_id)
        except Exception as exc:  # noqa: BLE001  # Persist background failures for polling clients.
            message = f"{type(exc).__name__}: {exc}"
            tb = traceback.format_exc()
            self._errors[session_id] = message
            session.status = "failed"
            report = _failure_report(session, self._settings, message)
            self._reports[session_id] = report
            session.completed_at = report.completed_at
            try:
                runtime = apply_settings_overrides(self._settings, session.config_overrides)
                await ArtifactStore(runtime.artifact_dir).save_session_report(report)
            except (OSError, TypeError, ValueError) as save_exc:
                _LOGGER.error(
                    "failure_report_persistence_failed",
                    session_id=session_id,
                    error_type=type(save_exc).__name__,
                )
            _LOGGER.error(
                "session_task_failed",
                session_id=session_id,
                error_type=type(exc).__name__,
                traceback=tb,
            )


def new_session_id() -> str:
    """Generate an opaque identifier suitable for URLs and artifact names."""

    return uuid4().hex


def _failure_report(session: TestSession, settings: Settings, error: str) -> SessionReport:
    try:
        runtime = apply_settings_overrides(settings, session.config_overrides)
        root = str(runtime.artifact_dir.resolve())
        ArtifactStore(runtime.artifact_dir).get_session_dir(session.session_id)
    except (OSError, TypeError, ValueError):
        root = str(settings.artifact_dir.resolve())
    completed_at = datetime.now(UTC)
    report = SessionReport(
        session_id=session.session_id,
        target_base_url=session.target_base_url,
        overall_status="error",
        summary=f"Session failed: {error}",
        total_duration_ms=0,
        artifact_root=root,
        completed_at=completed_at,
    )
    return report
