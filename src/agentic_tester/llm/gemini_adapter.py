"""Google Gemini implementation of the provider-neutral LLM adapter."""

import asyncio
import json
from pathlib import Path
from typing import Any, TypeVar

from google import genai
from google.genai import types
from pydantic import BaseModel

from agentic_tester.agent.action_space import ActionDecision
from agentic_tester.config import Settings
from agentic_tester.llm.base import LLMAdapter
from agentic_tester.llm.image_input import compressed_image_part
from agentic_tester.llm.projections import (
    HistoryProjection,
    compact_json,
    compact_mission,
    evidence_projection,
    history_projection,
    observation_projection,
)
from agentic_tester.llm.prompts import (
    ASSESS_ANOMALY_PROMPT,
    EVALUATE_BEHAVIOR_PROMPT,
    EXTRACT_REPRODUCTION_PROMPT,
    PLAN_ACTION_PROMPT,
    SESSION_SUMMARY_PROMPT,
    SUMMARIZE_HISTORY_PROMPT,
)
from agentic_tester.llm.schemas import (
    AnomalyAssessment,
    BehaviorVerdict,
    MissionContext,
)
from agentic_tester.logging import get_logger
from agentic_tester.models.evidence import Evidence
from agentic_tester.models.mission import ExpectedBehavior
from agentic_tester.models.observation import PageObservation
from agentic_tester.models.results import MissionResult, StepResult, TokenUsage

SchemaT = TypeVar("SchemaT", bound=BaseModel)
_LOGGER = get_logger(__name__)


class GeminiAdapter(LLMAdapter):
    """Gemini adapter with structured outputs, multimodal observations, and retries."""

    def __init__(
        self,
        settings: Settings,
        client: Any | None = None,
        max_retries: int = 3,
        retry_delay_seconds: float = 0.5,
    ) -> None:
        self._settings = settings
        self._client = client if client is not None else genai.Client(api_key=settings.gemini_api_key)
        self._max_retries = max(1, max_retries)
        self._retry_delay_seconds = max(0.0, retry_delay_seconds)
        self.token_usage = TokenUsage()

    async def plan_next_action(
        self,
        observation: PageObservation,
        mission_context: MissionContext,
        history_summary: str,
        recent_steps: list[StepResult],
    ) -> ActionDecision:
        prompt = PLAN_ACTION_PROMPT.format(
            mission_context=compact_json(mission_context),
            history_summary=history_summary or "No previous steps.",
            recent_steps=compact_json(history_projection(recent_steps)),
            observation=compact_json(observation_projection(
                observation,
                tree_max_chars=self._settings.observation_tree_max_chars,
                text_max_chars=self._settings.observation_text_max_chars,
            )),
        )
        return await self._generate(ActionDecision, prompt, observation, purpose="plan")

    async def evaluate_behavior(
        self,
        observation: PageObservation,
        expected: ExpectedBehavior,
        relevant_evidence: list[Evidence],
    ) -> BehaviorVerdict:
        prompt = EVALUATE_BEHAVIOR_PROMPT.format(
            expected=compact_json(expected),
            observation=compact_json(observation_projection(
                observation,
                tree_max_chars=self._settings.observation_tree_max_chars,
                text_max_chars=self._settings.observation_text_max_chars,
            )),
            evidence=compact_json(evidence_projection(
                relevant_evidence,
                excerpt_max_chars=self._settings.llm_evidence_excerpt_max_chars,
                max_items=self._settings.llm_max_evidence_items,
            )),
        )
        return await self._generate(BehaviorVerdict, prompt, observation, purpose="oracle")

    async def assess_anomaly(
        self,
        anomaly_description: str,
        observation: PageObservation,
        relevant_evidence: list[Evidence],
    ) -> AnomalyAssessment:
        prompt = ASSESS_ANOMALY_PROMPT.format(
            anomaly_description=anomaly_description,
            observation=compact_json(observation_projection(
                observation,
                tree_max_chars=self._settings.observation_tree_max_chars,
                text_max_chars=self._settings.observation_text_max_chars,
            )),
            evidence=compact_json(evidence_projection(
                relevant_evidence,
                excerpt_max_chars=self._settings.llm_evidence_excerpt_max_chars,
                max_items=self._settings.llm_max_evidence_items,
            )),
        )
        return await self._generate(AnomalyAssessment, prompt, observation, purpose="oracle")

    async def extract_reproduction_steps(
        self,
        anomaly: str,
        step_history: list[HistoryProjection],
    ) -> list[str]:
        prompt = EXTRACT_REPRODUCTION_PROMPT.format(
            anomaly=anomaly,
            step_history=compact_json(step_history),
        )
        response = await self._generate_raw(list[str], prompt)
        if isinstance(response, list):
            return [str(step) for step in response]
        raise TypeError("Gemini reproduction response was not a JSON array")

    async def summarize_history(self, steps: list[StepResult]) -> str:
        prompt = SUMMARIZE_HISTORY_PROMPT.format(steps=compact_json(history_projection(steps)))
        return await self._generate_text(prompt)

    async def generate_session_summary(self, mission_results: list[MissionResult]) -> str:
        prompt = SESSION_SUMMARY_PROMPT.format(
            mission_results=compact_json([compact_mission(result) for result in mission_results])
        )
        return await self._generate_text(prompt)

    async def _generate(
        self,
        schema: type[SchemaT],
        prompt: str,
        observation: PageObservation | None = None,
        *,
        purpose: str,
    ) -> SchemaT:
        response = await self._generate_response(schema, prompt, observation, purpose=purpose)
        return _parse_response(response, schema)

    async def _generate_raw(self, schema: Any, prompt: str) -> Any:
        response = await self._generate_response(schema, prompt, None, purpose="raw")
        parsed = getattr(response, "parsed", None)
        if parsed is not None:
            return parsed
        text = getattr(response, "text", None)
        if not text:
            raise ValueError("Gemini returned neither parsed content nor text")
        return json.loads(text)

    async def _generate_text(self, prompt: str) -> str:
        response = await self._generate_response(str, prompt, None, purpose="summary")
        text = getattr(response, "text", None)
        if text is None:
            parsed = getattr(response, "parsed", None)
            if isinstance(parsed, str):
                return parsed
            raise ValueError("Gemini returned no text content")
        return str(text).strip()

    async def _generate_response(
        self,
        schema: Any,
        prompt: str,
        observation: PageObservation | None,
        purpose: str,
    ) -> Any:
        contents: list[Any] = [prompt]
        image_bytes: int | None = None
        image_part = _image_part(
            observation,
            mode=self._settings.observation_image_mode,
            purpose=purpose,
            max_side=self._settings.observation_image_max_side,
            quality=self._settings.observation_image_quality,
        )
        if image_part is not None:
            contents.append(image_part)
            image_bytes = len(image_part.inline_data.data)

        config = types.GenerateContentConfig(
            response_mime_type="application/json" if schema is not str else "text/plain",
            response_schema=schema if schema is not str else None,
            temperature=self._settings.llm_temperature,
        )

        last_error: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                response = await self._client.aio.models.generate_content(
                    model=self._settings.llm_model,
                    contents=contents,
                    config=config,
                )
                self._record_usage(getattr(response, "usage_metadata", None))
                _LOGGER.info(
                    "llm_request_completed",
                    purpose=purpose,
                    prompt_chars=len(prompt),
                    image=image_bytes is not None,
                    image_bytes=image_bytes,
                )
                return response
            except Exception as exc:
                last_error = exc
                if attempt + 1 == self._max_retries:
                    raise
                await asyncio.sleep(self._retry_delay_seconds * (2**attempt))
        raise RuntimeError("Gemini request failed") from last_error

    def _record_usage(self, usage: Any) -> None:
        if usage is None:
            return
        input_tokens = _usage_value(usage, "prompt_token_count", "promptTokenCount")
        output_tokens = _usage_value(usage, "candidates_token_count", "candidatesTokenCount")
        total_tokens = _usage_value(usage, "total_token_count", "totalTokenCount")
        self.token_usage.input_tokens += input_tokens
        self.token_usage.output_tokens += output_tokens
        self.token_usage.total_tokens += total_tokens or input_tokens + output_tokens
        _LOGGER.info(
            "llm_usage_recorded",
            provider="gemini",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens or input_tokens + output_tokens,
        )


def _parse_response[SchemaT: BaseModel](response: Any, schema: type[SchemaT]) -> SchemaT:
    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, schema):
        return parsed
    if parsed is not None:
        return schema.model_validate(parsed)
    text = getattr(response, "text", None)
    if not text:
        raise ValueError("Gemini returned neither parsed content nor text")
    return schema.model_validate_json(text)


def _usage_value(usage: Any, *names: str) -> int:
    for name in names:
        value = getattr(usage, name, None)
        if value is not None:
            return int(value)
    return 0


def _image_part(
    observation: PageObservation | None,
    *,
    mode: str,
    purpose: str,
    max_side: int,
    quality: int,
) -> Any | None:
    if observation is None:
        return None
    if mode == "off" or (mode == "oracle" and purpose != "oracle"):
        return None
    return compressed_image_part(Path(observation.screenshot_path), max_side=max_side, quality=quality)
