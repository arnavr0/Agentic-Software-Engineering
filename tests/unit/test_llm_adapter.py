"""Provider adapter tests using a deterministic fake Gemini client."""

from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from google.genai import types
from PIL import Image

from agentic_tester.agent.action_space import ActionDecision, ActionType
from agentic_tester.config import Settings
from agentic_tester.llm.gemini_adapter import GeminiAdapter
from agentic_tester.llm.schemas import BehaviorVerdict as SchemaBehaviorVerdict
from agentic_tester.llm.schemas import MissionContext
from agentic_tester.models.evidence import Evidence, EvidenceType
from agentic_tester.models.mission import ExpectedBehavior
from agentic_tester.models.observation import PageObservation


class FakeModels:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    async def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeClient:
    def __init__(self, responses) -> None:
        self.models = FakeModels(responses)
        self.aio = SimpleNamespace(models=self.models)


def _observation(screenshot_path: str = "missing.png") -> PageObservation:
    return PageObservation(
        url="http://localhost:3000/",
        title="Demo",
        screenshot_path=screenshot_path,
        accessibility_tree='- button "Save" [el_0]',
        interactive_elements=[],
        visible_text_summary="Demo page",
        console_entries=[],
        network_log=[],
        state_fingerprint="abc123",
        timestamp="2026-08-22T00:00:00Z",
    )


def _context() -> MissionContext:
    return MissionContext(
        mission_id="mission-1",
        goal="Save a project",
        target_url="http://localhost:3000/",
        current_step_description="Click Save",
        expected_behaviors=[
            ExpectedBehavior(
                expectation_id="e1",
                description="Success appears",
                type="ui",
                severity="must",
            )
        ],
    )


@pytest.mark.asyncio
async def test_gemini_returns_structured_action_and_records_usage(tmp_path: Path) -> None:
    screenshot = tmp_path / "screen.png"
    Image.new("RGB", (100, 40), "red").save(screenshot, format="PNG")
    decision = ActionDecision(
        action_type=ActionType.CLICK,
        ref="el_0",
        reasoning="The save control advances the mission.",
        confidence=0.95,
        expected_result="A success message appears.",
    )
    response = SimpleNamespace(
        parsed=decision,
        usage_metadata=SimpleNamespace(
            prompt_token_count=10,
            candidates_token_count=5,
            total_token_count=15,
        ),
    )
    client = FakeClient([response])
    adapter = GeminiAdapter(Settings(llm_model="test-model"), client=client)

    result = await adapter.plan_next_action(_observation(str(screenshot)), _context(), "", [])

    assert result == decision
    assert adapter.token_usage.total_tokens == 15
    assert client.aio.models.calls[0]["model"] == "test-model"
    assert client.aio.models.calls[0]["config"].response_schema is ActionDecision
    assert len(client.aio.models.calls[0]["contents"]) == 1


@pytest.mark.asyncio
async def test_gemini_sends_downscaled_jpeg_observation(tmp_path: Path) -> None:
    screenshot = tmp_path / "screen.png"
    Image.new("RGB", (1536, 768), "blue").save(screenshot, format="PNG")
    response = SimpleNamespace(
        parsed=ActionDecision(
            action_type=ActionType.CLICK,
            ref="el_0",
            reasoning="The save control advances the mission.",
            confidence=0.95,
            expected_result="A success message appears.",
        ),
        text=None,
        usage_metadata=None,
    )
    client = FakeClient([response])
    adapter = GeminiAdapter(Settings(), client=client)

    await adapter.plan_next_action(_observation(str(screenshot)), _context(), "", [])

    assert len(client.aio.models.calls[0]["contents"]) == 1


@pytest.mark.asyncio
async def test_gemini_oracle_sends_downscaled_jpeg_observation(tmp_path: Path) -> None:
    screenshot = tmp_path / "screen.png"
    Image.new("RGB", (1536, 768), "blue").save(screenshot, format="PNG")
    response = SimpleNamespace(
            parsed=SchemaBehaviorVerdict(
            verdict="pass",
            confidence=0.9,
            explanation="Visible.",
        ),
        text=None,
        usage_metadata=None,
    )
    client = FakeClient([response])
    adapter = GeminiAdapter(Settings(observation_image_mode="oracle"), client=client)

    await adapter.evaluate_behavior(
        _observation(str(screenshot)),
        ExpectedBehavior(
            expectation_id="e1",
            description="Success appears",
            type="ui",
            severity="must",
        ),
        [],
    )

    image_part = client.aio.models.calls[0]["contents"][1]
    assert isinstance(image_part, types.Part)
    assert image_part.inline_data.mime_type == "image/jpeg"
    assert len(image_part.inline_data.data) < screenshot.stat().st_size
    with Image.open(BytesIO(image_part.inline_data.data)) as image:
        assert image.size == (512, 256)


@pytest.mark.asyncio
async def test_gemini_planning_prompt_is_compact_and_semantic(tmp_path: Path) -> None:
    screenshot = tmp_path / "screen.png"
    Image.new("RGB", (1280, 720), "blue").save(screenshot, format="PNG")
    decision = ActionDecision(
        action_type=ActionType.CLICK,
        ref="el_0",
        reasoning="The save control advances the mission.",
        confidence=0.95,
        expected_result="A success message appears.",
    )
    response = SimpleNamespace(parsed=decision, text=None, usage_metadata=None)
    client = FakeClient([response])
    adapter = GeminiAdapter(
        Settings(observation_image_mode="off", observation_tree_max_chars=100),
        client=client,
    )

    await adapter.plan_next_action(_observation(str(screenshot)), _context(), "", [])

    prompt = client.aio.models.calls[0]["contents"][0]
    assert "# Raw Playwright ARIA snapshot:" not in prompt
    assert "locator_value" not in prompt
    assert "screenshot_path" not in prompt
    assert len(prompt) < 1_000
    assert len(client.aio.models.calls[0]["contents"]) == 1


@pytest.mark.asyncio
async def test_gemini_oracle_uses_bounded_evidence(tmp_path: Path) -> None:
    screenshot = tmp_path / "screen.png"
    Image.new("RGB", (1280, 720), "blue").save(screenshot, format="PNG")
    evidence = Evidence(
        type=EvidenceType.ACCESSIBILITY,
        source="perception",
        excerpt="- button " + ("x" * 500),
    )
    response = SimpleNamespace(
        parsed=SchemaBehaviorVerdict(
            verdict="pass",
            confidence=0.9,
            explanation="Visible.",
        ),
        text=None,
        usage_metadata=None,
    )
    client = FakeClient([response])
    adapter = GeminiAdapter(Settings(observation_image_mode="oracle"), client=client)

    await adapter.evaluate_behavior(
        _observation(str(screenshot)),
        ExpectedBehavior(
            expectation_id="e1",
            description="Saved",
            type="ui",
            severity="must",
        ),
        [evidence],
    )

    contents = client.aio.models.calls[0]["contents"]
    assert '"type":"accessibility"' in contents[0]
    assert "x" * 400 not in contents[0]
    assert len(contents) == 2
    assert contents[1].inline_data.mime_type == "image/jpeg"


@pytest.mark.asyncio
async def test_gemini_parses_text_fallback_and_retries() -> None:
    response = SimpleNamespace(
        parsed=None,
        text='{"verdict":"pass","confidence":0.8,"explanation":"Visible","evidence_ids":[]}',
        usage_metadata=None,
    )
    client = FakeClient([RuntimeError("temporary rate limit"), response])
    adapter = GeminiAdapter(client=client, settings=Settings(), max_retries=2, retry_delay_seconds=0)

    result = await adapter.evaluate_behavior(
        _observation(),
        ExpectedBehavior(
            expectation_id="e1",
            description="Success appears",
            type="ui",
            severity="must",
        ),
        [],
    )

    assert result.verdict == "pass"
    assert len(client.aio.models.calls) == 2
