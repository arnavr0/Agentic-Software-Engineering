"""Configuration defaults and environment loading tests."""

from pathlib import Path

from agentic_tester.config import Settings


def test_settings_defaults_are_usable() -> None:
    settings = Settings()

    assert settings.llm_provider == "gemini"
    assert settings.llm_model
    assert settings.browser_type == "chromium"
    assert settings.headless is True
    assert settings.artifact_dir == Path("artifacts")
    assert settings.max_actions_per_mission == 50


def test_gemini_key_accepts_conventional_environment_name(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    assert Settings().gemini_api_key == "test-key"


def test_prefixed_environment_values_override_defaults(monkeypatch) -> None:
    monkeypatch.setenv("TESTER_PORT", "9123")
    monkeypatch.setenv("TESTER_HEADLESS", "false")

    settings = Settings()

    assert settings.port == 9123
    assert settings.headless is False
