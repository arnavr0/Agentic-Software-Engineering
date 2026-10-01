"""Abstract interface implemented by concrete LLM providers."""

from abc import ABC, abstractmethod

from agentic_tester.agent.action_space import ActionDecision
from agentic_tester.llm.projections import HistoryProjection
from agentic_tester.llm.schemas import (
    AnomalyAssessment,
    BehaviorVerdict,
    MissionContext,
)
from agentic_tester.models.evidence import Evidence
from agentic_tester.models.mission import ExpectedBehavior
from agentic_tester.models.observation import PageObservation
from agentic_tester.models.results import MissionResult, StepResult


class LLMAdapter(ABC):
    """Provider-neutral contract for all model-assisted tester decisions."""

    @abstractmethod
    async def plan_next_action(
        self,
        observation: PageObservation,
        mission_context: MissionContext,
        history_summary: str,
        recent_steps: list[StepResult],
    ) -> ActionDecision:
        """Choose the next action for a guided mission step."""

    @abstractmethod
    async def evaluate_behavior(
        self,
        observation: PageObservation,
        expected: ExpectedBehavior,
        relevant_evidence: list[Evidence],
    ) -> BehaviorVerdict:
        """Evaluate one expected behavior against observed evidence."""

    @abstractmethod
    async def assess_anomaly(
        self,
        anomaly_description: str,
        observation: PageObservation,
        relevant_evidence: list[Evidence],
    ) -> AnomalyAssessment:
        """Classify an observed anomaly and estimate its confidence."""

    @abstractmethod
    async def extract_reproduction_steps(
        self,
        anomaly: str,
        step_history: list[HistoryProjection],
    ) -> list[str]:
        """Extract a concise, human-readable reproduction sequence."""

    @abstractmethod
    async def summarize_history(self, steps: list[StepResult]) -> str:
        """Summarize older execution steps for context compaction."""

    @abstractmethod
    async def generate_session_summary(self, mission_results: list[MissionResult]) -> str:
        """Generate a concise summary from compact mission projections."""
