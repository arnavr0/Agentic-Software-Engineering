"""Factory for selecting the configured LLM provider."""

from agentic_tester.config import Settings
from agentic_tester.llm.base import LLMAdapter
from agentic_tester.llm.gemini_adapter import GeminiAdapter


def create_llm_adapter(settings: Settings) -> LLMAdapter:
    """Create the adapter configured in ``settings``."""

    if settings.llm_provider == "gemini":
        return GeminiAdapter(settings)
    raise ValueError(f"Unsupported LLM provider: {settings.llm_provider}")
