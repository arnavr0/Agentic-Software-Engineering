"""Sliding-window context management for long-running mission histories."""

from agentic_tester.llm.base import LLMAdapter
from agentic_tester.models.results import StepResult


class ContextManager:
    """Retain recent steps in full and summarize older steps on demand."""

    def __init__(self, window_size: int = 10, llm: LLMAdapter | None = None) -> None:
        if window_size < 1:
            raise ValueError("window_size must be at least 1")
        self._full_history: list[StepResult] = []
        self._summary = ""
        self._window_size = window_size
        self._llm = llm
        self._summarized_count = 0

    def add_step(self, step: StepResult) -> None:
        """Append one completed step to the immutable-in-practice history."""

        self._full_history.append(step)

    async def get_context(self) -> tuple[str, list[StepResult]]:
        """Return ``(summary_of_old_steps, recent_steps_in_full_detail)``."""

        cutoff = max(0, len(self._full_history) - self._window_size)
        if cutoff > self._summarized_count:
            old_steps = self._full_history[:cutoff]
            if self._llm is not None:
                self._summary = await self._llm.summarize_history(old_steps)
            else:
                self._summary = _deterministic_summary(old_steps)
            self._summarized_count = cutoff
        return self._summary, list(self._full_history[cutoff:])

    def get_full_history(self) -> list[StepResult]:
        """Return a copy so callers cannot mutate the manager's history accidentally."""

        return list(self._full_history)

    def reset(self) -> None:
        """Clear history and its rolling summary."""

        self._full_history.clear()
        self._summary = ""
        self._summarized_count = 0


def _deterministic_summary(steps: list[StepResult]) -> str:
    if not steps:
        return ""
    lines = []
    for step in steps:
        status = "blocked" if step.was_blocked else "error" if step.error else "completed"
        lines.append(
            f"Step {step.step_index} ({step.step_id or 'unnamed'}): "
            f"{step.action_taken}; {status}; URL {step.page_url_after}"
        )
    return "\n".join(lines)
