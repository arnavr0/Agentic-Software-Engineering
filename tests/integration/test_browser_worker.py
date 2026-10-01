"""Live Playwright verification for the browser worker and perception engine."""

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from agentic_tester.agent.action_space import ActionDecision, ActionType
from agentic_tester.browser.perception import PerceptionEngine
from agentic_tester.browser.worker import BrowserWorker
from agentic_tester.config import Settings

FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "test_app"


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:
        return


class ScreenshotStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    async def save_screenshot(
        self,
        session_id: str,
        mission_id: str,
        step_index: int,
        data: bytes,
    ) -> str:
        path = self.root / session_id / mission_id / f"{step_index:04d}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return str(path.relative_to(self.root)).replace("\\", "/")


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
async def test_worker_captures_and_executes_against_fixture(fixture_server, tmp_path: Path) -> None:
    worker = BrowserWorker()
    settings = Settings(headless=True, default_timeout_ms=5_000)
    store = ScreenshotStore(tmp_path / "artifacts")
    perception = PerceptionEngine()
    trace_path = tmp_path / "traces" / "mission.zip"

    await worker.start_browser(settings)
    try:
        await worker.create_context(settings)
        await worker.navigate(fixture_server)
        initial = await perception.capture(worker, store, "session-1", "mission-1", 0)

        assert initial.title == "Agentic Tester Fixture"
        assert {element.name for element in initial.interactive_elements} >= {
            "Project name",
            "Priority",
            "Save project",
        }
        save = next(element for element in initial.interactive_elements if element.name == "Save project")
        input_element = next(
            element for element in initial.interactive_elements if element.name == "Project name"
        )
        fill_error = await worker.execute_action(
            ActionDecision(
                action_type=ActionType.FILL,
                ref=input_element.id,
                value="Demo project",
                reasoning="Provide valid form data.",
                confidence=1.0,
                expected_result="The input contains the project name.",
            ),
            initial.interactive_elements,
        )
        assert fill_error is None
        click_error = await worker.execute_action(
            ActionDecision(
                action_type=ActionType.CLICK,
                ref=save.id,
                reasoning="Submit the form.",
                confidence=1.0,
                expected_result="The success status appears.",
            ),
            initial.interactive_elements,
        )
        assert click_error is None
        await worker.wait(100)
        after_click = await perception.capture(worker, store, "session-1", "mission-1", 1)

        assert "Project saved" in after_click.visible_text_summary
        assert any(entry.text == "fixture loaded" for entry in initial.console_entries)
        assert any(entry.url.endswith("/api/health") and entry.is_failure for entry in after_click.network_log)
        assert (tmp_path / "artifacts" / "session-1" / "mission-1" / "0001.png").is_file()

        await worker.close_context(trace_path)
        assert trace_path.is_file()
        assert trace_path.stat().st_size > 0

        # A second context starts with a clean page state and no prior form submission.
        await worker.create_context(settings)
        await worker.navigate(fixture_server)
        isolated = await perception.capture(worker, store, "session-1", "mission-2", 0)
        assert "Project saved" not in isolated.visible_text_summary
    finally:
        await worker.stop_browser()
