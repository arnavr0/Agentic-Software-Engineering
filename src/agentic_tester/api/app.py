"""FastAPI entry point for tester sessions."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse

from agentic_tester.config import Settings
from agentic_tester.logging import configure_logging
from agentic_tester.models.results import SessionReport
from agentic_tester.models.session import TestSession

from .schemas import (
    CancelSessionResponse,
    CreateSessionRequest,
    SessionCreatedResponse,
    SessionStatusResponse,
)
from .session_manager import SessionManager, new_session_id


def create_app(
    settings: Settings | None = None,
    manager: SessionManager | None = None,
) -> FastAPI:
    """Build an app with injectable settings and manager for API tests."""

    runtime_settings = settings or Settings()
    configure_logging(runtime_settings.log_level)
    session_manager = manager or SessionManager(runtime_settings)
    app = FastAPI(title="Agentic Tester", version="0.1.0")
    app.state.session_manager = session_manager

    @app.post("/sessions", response_model=SessionCreatedResponse, status_code=202)
    async def create_session(request: CreateSessionRequest) -> SessionCreatedResponse:
        session = TestSession(
            session_id=new_session_id(),
            target_base_url=request.target_base_url,
            missions=request.missions,
            execution_policy=request.execution_policy,
            config_overrides=request.config_overrides,
            created_at=datetime.now(UTC),
        )
        try:
            await session_manager.create_session(session)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return SessionCreatedResponse(
            session_id=session.session_id,
            status=session.status,
            created_at=session.created_at,
        )

    @app.get("/sessions/{session_id}", response_model=SessionStatusResponse)
    async def get_session_status(session_id: str) -> SessionStatusResponse:
        session = _require_session(session_manager, session_id)
        report = session_manager.get_report(session_id)
        return SessionStatusResponse(
            session_id=session.session_id,
            status=session.status,
            created_at=session.created_at,
            started_at=session.started_at,
            completed_at=session.completed_at or (report.completed_at if report else None),
            overall_status=report.overall_status if report else None,
            total_bugs=report.total_bugs if report else None,
            error=session_manager.get_error(session_id),
        )

    @app.get("/sessions/{session_id}/report", response_model=SessionReport)
    async def get_session_report(session_id: str) -> SessionReport:
        _require_session(session_manager, session_id)
        report = session_manager.get_report(session_id)
        if report is None:
            raise HTTPException(status_code=404, detail="session report is not ready")
        return report

    @app.get("/sessions/{session_id}/artifacts/{path:path}")
    async def get_artifact(session_id: str, path: str) -> FileResponse:
        _require_session(session_manager, session_id)
        artifact = session_manager.artifact_path(session_id, path)
        if artifact is None:
            raise HTTPException(status_code=404, detail="artifact not found")
        return FileResponse(artifact)

    @app.post("/sessions/{session_id}/cancel", response_model=CancelSessionResponse)
    async def cancel_session(session_id: str) -> CancelSessionResponse:
        session = await session_manager.cancel_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="session not found")
        return CancelSessionResponse(session_id=session.session_id, status=session.status)

    @app.get("/health")
    async def health_check(request: Request) -> dict[str, str]:
        del request
        return {"status": "ok"}

    return app


def _require_session(manager: SessionManager, session_id: str) -> TestSession:
    session = manager.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    return session


app = create_app()
