"""Real API-to-session-engine smoke test against the local fixture."""

import asyncio
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import httpx
import pytest
from tests.integration.test_action_loop import DeterministicLLM

from agentic_tester.api.app import create_app
from agentic_tester.api.session_manager import SessionManager
from agentic_tester.config import Settings
from agentic_tester.engine.session import SessionEngine

FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "test_app"


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:
        del format, args


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
async def test_api_runs_real_session_engine_against_local_fixture(
    fixture_server: str,
    tmp_path: Path,
) -> None:
    settings = Settings(
        artifact_dir=tmp_path / "artifacts",
        default_timeout_ms=5_000,
        max_retries_per_action=0,
    )
    manager = SessionManager(
        settings,
        engine_factory=lambda runtime: SessionEngine(runtime, llm=DeterministicLLM()),
    )
    payload = {
        "target_base_url": fixture_server,
        "missions": [
            {
                "mission_id": "api-smoke",
                "goal": "Load the fixture",
                "target_url": fixture_server,
                "actions": [],
            }
        ],
    }

    transport = httpx.ASGITransport(app=create_app(settings, manager))
    async with httpx.AsyncClient(transport=transport, base_url="http://tester") as client:
        created = await client.post("/sessions", json=payload)
        assert created.status_code == 202
        session_id = created.json()["session_id"]

        status = None
        for _ in range(200):
            status = await client.get(f"/sessions/{session_id}")
            if status.json()["status"] in {"completed", "failed"}:
                break
            await asyncio.sleep(0.1)
        assert status is not None
        assert status.json()["status"] == "completed", status.text
        report = await client.get(f"/sessions/{session_id}/report")
        assert report.status_code == 200
        assert report.json()["overall_status"] == "passed"
        artifact = await client.get(f"/sessions/{session_id}/artifacts/report.json")
        assert artifact.status_code == 200
