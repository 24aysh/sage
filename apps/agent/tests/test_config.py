import pytest

from sage.config import (
    DEFAULT_OPENAI_MODEL,
    DEFAULT_V2_PLANNER_FALLBACK_MODEL,
    DEFAULT_V2_PLANNER_MODEL,
    DEFAULT_V2_REVIEWER_MODEL,
    DEFAULT_V2_SOLVER_MODEL,
    Settings,
)
from sage.errors import ConfigurationError


def test_settings_loads_all_supported_environment_values() -> None:
    settings = Settings.from_env(
        {
            "OPENAI_API_KEY": "secret",
            "OPENAI_MODEL": "test-model",
            "OPENAI_MAX_RETRIES": "4",
            "SAGE_MAX_TURNS": "12",
            "SAGE_RUNS_DIR": "/tmp/test-runs",
            "SAGE_SANDBOX_IMAGE": "custom:v0",
            "SAGE_COMMAND_TIMEOUT_SECONDS": "20",
            "SAGE_MAX_TOOL_OUTPUT_CHARS": "2000",
            "LANGSMITH_TRACING": "true",
            "LANGSMITH_API_KEY": "langsmith-secret",
            "LANGSMITH_PROJECT": "sage-test",
            "LANGSMITH_WORKSPACE_ID": "workspace-123",
        }
    )

    assert settings.openai_api_key == "secret"
    assert settings.openai_model == "test-model"
    assert settings.openai_max_retries == 4
    assert settings.max_turns == 12
    assert str(settings.runs_dir) == "/tmp/test-runs"
    assert settings.sandbox_image == "custom:v0"
    assert settings.command_timeout_seconds == 20
    assert settings.max_tool_output_chars == 2_000
    assert settings.langsmith_tracing is True
    assert settings.langsmith_project == "sage-test"
    assert settings.langsmith_workspace_id == "workspace-123"
    assert "langsmith-secret" not in repr(settings)


def test_settings_uses_the_project_default_openai_model() -> None:
    settings = Settings.from_env({"OPENAI_API_KEY": "secret"})

    assert settings.openai_model == DEFAULT_OPENAI_MODEL == "gpt-5.4-mini"


def test_v2_settings_require_locked_profile_credentials_and_acknowledgement() -> None:
    settings = Settings.from_env(
        {
            "SAGE_RUNTIME": "v2-prototype",
            "SAGE_MODEL_PROFILE": "constrained-cross-provider",
            "SAGE_GOOGLE_MODEL_CONTEXT_APPROVED": "true",
            "GEMINI_API_KEY": "gemini-secret",
            "OPENAI_API_KEY": "openai-secret",
            "SAGE_V2_PLANNER_MODEL": "custom-planner",
            "SAGE_V2_PLANNER_FALLBACK_MODEL": "custom-planner-fallback",
            "SAGE_V2_SOLVER_MODEL": "custom-solver",
            "SAGE_V2_REVIEWER_MODEL": "custom-reviewer",
            "SAGE_VERIFICATION_COMMANDS_JSON": (
                '[{"id":"focused","command":"pytest -q tests/test_app.py",'
                '"required":true,"timeout_seconds":20}]'
            ),
        }
    )

    assert settings.runtime == "v2-prototype"
    assert settings.v2_planner_model == "custom-planner"
    assert settings.v2_planner_fallback_model == "custom-planner-fallback"
    assert settings.v2_solver_model == "custom-solver"
    assert settings.v2_reviewer_model == "custom-reviewer"
    assert settings.verification_commands[0].check_id == "focused"
    assert "secret" not in repr(settings)


def test_v2_settings_use_documented_default_models() -> None:
    settings = Settings.from_env(
        {
            "SAGE_RUNTIME": "v2-prototype",
            "GEMINI_API_KEY": "gemini-secret",
            "OPENAI_API_KEY": "openai-secret",
        }
    )

    assert settings.google_model_context_approved is True
    assert settings.v2_planner_model == DEFAULT_V2_PLANNER_MODEL == "gemini-3.5-flash"
    assert (
        settings.v2_planner_fallback_model
        == DEFAULT_V2_PLANNER_FALLBACK_MODEL
        == "gemini-3.5-flash-lite"
    )
    assert settings.v2_solver_model == DEFAULT_V2_SOLVER_MODEL == "gpt-5.4-mini"
    assert settings.v2_reviewer_model == DEFAULT_V2_REVIEWER_MODEL == "gemini-3.5-flash"


def test_v2_settings_require_gemini_credentials() -> None:
    with pytest.raises(ConfigurationError, match="GEMINI_API_KEY"):
        Settings.from_env(
            {
                "SAGE_RUNTIME": "v2-prototype",
                "OPENAI_API_KEY": "openai-secret",
            }
        )


def test_v2_settings_respect_explicit_google_context_rejection() -> None:
    with pytest.raises(ConfigurationError, match="CONTEXT_APPROVED"):
        Settings.from_env(
            {
                "SAGE_RUNTIME": "v2-prototype",
                "SAGE_GOOGLE_MODEL_CONTEXT_APPROVED": "false",
                "GEMINI_API_KEY": "gemini-secret",
                "OPENAI_API_KEY": "openai-secret",
            }
        )


def test_v1_does_not_require_v2_credentials() -> None:
    settings = Settings.from_env({"OPENAI_API_KEY": "openai-secret"})

    assert settings.runtime == "v1"
    assert settings.gemini_api_key is None


def test_langsmith_tracing_requires_api_key() -> None:
    with pytest.raises(ConfigurationError, match="LANGSMITH_API_KEY"):
        Settings.from_env(
            {
                "OPENAI_API_KEY": "openai-secret",
                "LANGSMITH_TRACING": "true",
            }
        )


def test_langsmith_defaults_to_disabled_named_project() -> None:
    settings = Settings.from_env({"OPENAI_API_KEY": "openai-secret"})

    assert settings.langsmith_tracing is False
    assert settings.langsmith_api_key is None
    assert settings.langsmith_project == "sage-v2"


@pytest.mark.parametrize(
    "name",
    [
        "SAGE_V2_PLANNER_MODEL",
        "SAGE_V2_PLANNER_FALLBACK_MODEL",
        "SAGE_V2_SOLVER_MODEL",
        "SAGE_V2_REVIEWER_MODEL",
    ],
)
def test_settings_rejects_empty_v2_model_names(name: str) -> None:
    with pytest.raises(ConfigurationError, match="Invalid Sage configuration"):
        Settings.from_env(
            {
                "OPENAI_API_KEY": "openai-secret",
                name: " ",
            }
        )


def test_settings_rejects_missing_api_key() -> None:
    with pytest.raises(ConfigurationError, match="OPENAI_API_KEY"):
        Settings.from_env({})


def test_settings_rejects_invalid_bounds() -> None:
    with pytest.raises(ConfigurationError, match="Invalid Sage configuration"):
        Settings.from_env(
            {
                "OPENAI_API_KEY": "secret",
                "SAGE_MAX_TURNS": "0",
            }
        )


@pytest.mark.parametrize("value", ["-1", "11", "invalid"])
def test_settings_rejects_invalid_openai_retry_limit(value: str) -> None:
    with pytest.raises(ConfigurationError, match="Invalid Sage configuration"):
        Settings.from_env(
            {
                "OPENAI_API_KEY": "secret",
                "OPENAI_MAX_RETRIES": value,
            }
        )
