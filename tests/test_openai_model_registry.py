from __future__ import annotations

import json

import pytest

from eligibility.cli import main
from eligibility.llm import (
    DEFAULT_OPENAI_MODEL,
    GENERIC_RESPONSES_PROFILE,
    GPT_5_6_RESPONSES_PROFILE,
    LLMConfigurationError,
    LLMSettings,
    OpenAIResponsesAdapter,
    resolve_openai_responses_model_profile,
)


def test_gpt56_models_and_snapshots_resolve_to_one_profile() -> None:
    for model in (
        "gpt-5.6-luna",
        "gpt-5.6-terra",
        "gpt-5.6-sol",
        "gpt-5.6",
        "gpt-5.6-luna-2026-08-01",
    ):
        assert resolve_openai_responses_model_profile(model) is GPT_5_6_RESPONSES_PROFILE


def test_gpt56_profile_builds_reasoning_controls_without_temperature() -> None:
    controls = GPT_5_6_RESPONSES_PROFILE.generation_controls(
        temperature=0.75,
        reasoning_effort="auto",
    )

    assert controls == {"reasoning": {"effort": "low"}}


def test_gpt56_profile_rejects_invalid_reasoning_effort_early() -> None:
    with pytest.raises(ValueError, match="does not support reasoning effort"):
        GPT_5_6_RESPONSES_PROFILE.resolve_reasoning_effort("minimal")


def test_openai_adapter_reports_invalid_model_controls_as_configuration_error() -> None:
    with pytest.raises(LLMConfigurationError, match="reasoning effort"):
        OpenAIResponsesAdapter(
            base_url="https://api.openai.com/v1",
            model="gpt-5.6-luna",
            api_key="secret",
            reasoning_effort="minimal",
        )


def test_unknown_model_uses_backward_compatible_generic_profile() -> None:
    profile = resolve_openai_responses_model_profile("future-model")

    assert profile is GENERIC_RESPONSES_PROFILE
    assert profile.generation_controls(
        temperature=0.25,
        reasoning_effort=None,
    ) == {"temperature": 0.25}


def test_openai_auto_settings_resolve_to_luna_profile_defaults() -> None:
    settings = LLMSettings.from_env(
        {
            "LLM_PROVIDER": "OPENAI",
            "LLM_BASE_URL": "auto",
            "LLM_MODEL": "auto",
            "LLM_REASONING_EFFORT": "auto",
            "OPENAI_API_KEY": "secret",
        }
    )

    assert settings.model == DEFAULT_OPENAI_MODEL == "gpt-5.6-luna"
    assert settings.base_url == "https://api.openai.com/v1"
    assert settings.reasoning_effort == "low"


def test_switching_to_generic_model_changes_defaults_without_adapter_edits() -> None:
    settings = LLMSettings.from_env(
        {
            "LLM_PROVIDER": "OPENAI",
            "LLM_MODEL": "gpt-4.1",
            "LLM_REASONING_EFFORT": "auto",
            "OPENAI_API_KEY": "secret",
        }
    )
    profile = resolve_openai_responses_model_profile(settings.model)

    assert profile is GENERIC_RESPONSES_PROFILE
    assert settings.reasoning_effort is None
    assert profile.supports_temperature is True


def test_llm_config_reports_profile_without_exposing_key(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "OPENAI")
    monkeypatch.setenv("LLM_MODEL", "gpt-5.6-luna")
    monkeypatch.setenv("LLM_REASONING_EFFORT", "auto")
    monkeypatch.setenv("OPENAI_API_KEY", "never-print-this-key")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)

    assert main(["llm-config"]) == 0

    output = capsys.readouterr().out
    summary = json.loads(output)
    assert summary["model_profile"] == "gpt-5.6"
    assert summary["request_controls"]["temperature"] == "OMITTED"
    assert summary["request_controls"]["reasoning_effort"] == "low"
    assert summary["credential_configured"] is True
    assert "never-print-this-key" not in output
