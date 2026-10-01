"""Application configuration loaded from environment variables and an optional .env file."""

from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, TypeAdapter
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for the tester.

    All settings except the Gemini key use the ``TESTER_`` environment prefix. The
    Gemini key accepts both ``TESTER_GEMINI_API_KEY`` and ``GEMINI_API_KEY`` so the
    conventional provider variable works without weakening namespacing elsewhere.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="TESTER_",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # LLM
    llm_provider: Literal["gemini"] = "gemini"
    gemini_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("TESTER_GEMINI_API_KEY", "GEMINI_API_KEY"),
    )
    llm_model: str = "gemini-3.6-flash"
    llm_temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    observation_image_mode: Literal["off", "oracle", "always"] = "oracle"
    observation_image_max_side: int = Field(default=512, gt=0)
    observation_image_quality: int = Field(default=60, ge=1, le=95)
    observation_tree_max_chars: int = Field(default=6000, gt=0)
    observation_text_max_chars: int = Field(default=1200, gt=0)
    llm_evidence_excerpt_max_chars: int = Field(default=300, gt=0)
    llm_max_evidence_items: int = Field(default=12, gt=0)

    # Browser
    browser_type: Literal["chromium", "firefox", "webkit"] = "chromium"
    headless: bool = True
    viewport_width: int = Field(default=1280, gt=0)
    viewport_height: int = Field(default=720, gt=0)
    default_timeout_ms: int = Field(default=30000, gt=0)
    slow_mo_ms: int = Field(default=0, ge=0)

    # Server
    host: str = "0.0.0.0"
    port: int = Field(default=8000, ge=1, le=65535)

    # Artifacts
    artifact_dir: Path = Path("./artifacts")

    # Agent behavior
    max_actions_per_mission: int = Field(default=50, gt=0)
    max_retries_per_action: int = Field(default=3, ge=0)
    max_minimization_attempts: int = Field(default=20, gt=0)

    # Context management
    context_window_size: int = Field(default=3, gt=0)

    # Logging
    log_level: str = "INFO"


def apply_settings_overrides(settings: Settings, overrides: dict[str, object]) -> Settings:
    """Return validated settings with a finite set of field-name overrides.

    API clients may tune runtime behavior for one session, but arbitrary model
    keys must never be copied into the process configuration.  Validation is
    performed against the declared Pydantic field type before the copy is made.
    """

    unknown = sorted(set(overrides).difference(Settings.model_fields))
    if unknown:
        raise ValueError(f"Unsupported settings override(s): {', '.join(unknown)}")

    validated: dict[str, object] = {}
    for name, value in overrides.items():
        field = Settings.model_fields[name]
        validated[name] = TypeAdapter(field.annotation).validate_python(value)
    return settings.model_copy(update=validated)
