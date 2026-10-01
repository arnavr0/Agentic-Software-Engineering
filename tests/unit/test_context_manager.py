"""Sliding-window and rolling-summary tests."""

from unittest.mock import AsyncMock, Mock

import pytest

from agentic_tester.agent.context_manager import ContextManager
from agentic_tester.llm.base import LLMAdapter
from agentic_tester.models.results import StepResult


def _step(index: int) -> StepResult:
    return StepResult(
        step_index=index,
        action_taken=f"click el_{index}",
        action_type="click",
        reasoning="test",
        page_url_before="http://localhost/",
        page_url_after=f"http://localhost/{index}",
        duration_ms=1,
        timestamp="2026-08-22T00:00:00Z",
    )


@pytest.mark.asyncio
async def test_context_manager_summarizes_only_steps_outside_window() -> None:
    llm = Mock(spec=LLMAdapter)
    llm.summarize_history = AsyncMock(return_value="Older steps summarized")
    manager = ContextManager(window_size=2, llm=llm)
    for index in range(3):
        manager.add_step(_step(index))

    summary, recent = await manager.get_context()

    assert summary == "Older steps summarized"
    assert [step.step_index for step in recent] == [1, 2]
    llm.summarize_history.assert_awaited_once_with([manager.get_full_history()[0]])


@pytest.mark.asyncio
async def test_context_manager_has_deterministic_fallback_and_reset() -> None:
    manager = ContextManager(window_size=1)
    manager.add_step(_step(0))
    manager.add_step(_step(1))

    summary, recent = await manager.get_context()

    assert "Step 0" in summary
    assert [step.step_index for step in recent] == [1]
    manager.reset()
    assert manager.get_full_history() == []
    assert await manager.get_context() == ("", [])
