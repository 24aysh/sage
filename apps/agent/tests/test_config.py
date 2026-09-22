import pytest

from sage.config import (
    DEFAULT_REVIEWER_MODEL,
    DEFAULT_SOLVER_MODEL,
    Settings,
)
from sage.errors import ConfigurationError


def test_settings_loads_all_supported_environment_values() -> None:
    settings = Settings.from_env(
        {
            "OPENAI_API_KEY": "secret",
            "GEMINI_API_KEY": "gemini-secret",
            "OPENAI_MAX_RETRIES": "4",
            "SAGE_MAX_TURNS": "12",
            "SAGE_RUNS_DIR": "/tmp/test-runs",
            "SAGE_SANDBOX_IMAGE": "custom:test",
            "SAGE_COMMAND_TIMEOUT_SECONDS": "20",
            "SAGE_MAX_TOOL_OUTPUT_CHARS": "2000",
            "LANGSMITH_TRACING": "true",
            "LANGSMITH_API_KEY": "langsmith-secret",
            "LANGSMITH_PROJECT": "sage-test",
            "LANGSMITH_WORKSPACE_ID": "workspace-123",
        }
    )

    assert settings.openai_api_key == "secret"
    assert settings.openai_max_retries == 4
    assert settings.max_turns == 12
    assert str(settings.runs_dir) == "/tmp/test-runs"
    assert settings.sandbox_image == "custom:test"
    assert settings.command_timeout_seconds == 20
    assert settings.max_tool_output_chars == 2_000
    assert settings.langsmith_tracing is True
    assert settings.langsmith_project == "sage-test"
    assert settings.langsmith_workspace_id == "workspace-123"
    assert "langsmith-secret" not in repr(settings)


def test_settings_uses_documented_models_and_sandbox_by_default() -> None:
    settings = Settings.from_env(
        {"OPENAI_API_KEY": "secret", "GEMINI_API_KEY": "gemini-secret"}
    )

    assert settings.solver_model == DEFAULT_SOLVER_MODEL == "gpt-5.4-mini"
    assert settings.reviewer_model == DEFAULT_REVIEWER_MODEL == "gemini-3.5-flash"
    assert settings.sandbox_image == "sage-sandbox:v2"
    assert not hasattr(settings, "runtime")
    assert not hasattr(settings, "model_profile")


def test_settings_loads_role_models_and_verification_commands() -> None:
    settings = Settings.from_env(
        {
            "SAGE_GOOGLE_MODEL_CONTEXT_APPROVED": "true",
            "GEMINI_API_KEY": "gemini-secret",
            "OPENAI_API_KEY": "openai-secret",
            "SOLVER_MODEL": "custom-solver",
            "REVIEWER_MODEL": "custom-reviewer",
            "SAGE_VERIFICATION_COMMANDS_JSON": (
                '[{"id":"focused","command":"pytest -q tests/test_app.py",'
                '"required":true,"timeout_seconds":20}]'
            ),
        }
    )

    assert settings.solver_model == "custom-solver"
    assert settings.reviewer_model == "custom-reviewer"
    assert settings.verification_commands[0].check_id == "focused"
    assert "secret" not in repr(settings)


def test_settings_use_documented_default_models() -> None:
    settings = Settings.from_env(
        {
            "GEMINI_API_KEY": "gemini-secret",
            "OPENAI_API_KEY": "openai-secret",
        }
    )

    assert settings.google_model_context_approved is True
    assert settings.solver_model == DEFAULT_SOLVER_MODEL == "gpt-5.4-mini"
    assert settings.reviewer_model == DEFAULT_REVIEWER_MODEL == "gemini-3.5-flash"


def test_settings_require_gemini_credentials() -> None:
    with pytest.raises(ConfigurationError, match="GEMINI_API_KEY"):
        Settings.from_env(
            {
                "OPENAI_API_KEY": "openai-secret",
            }
        )


def test_settings_respect_explicit_google_context_rejection() -> None:
    with pytest.raises(ConfigurationError, match="CONTEXT_APPROVED"):
        Settings.from_env(
            {
                "SAGE_GOOGLE_MODEL_CONTEXT_APPROVED": "false",
                "GEMINI_API_KEY": "gemini-secret",
                "OPENAI_API_KEY": "openai-secret",
            }
        )


def test_langsmith_tracing_requires_api_key() -> None:
    with pytest.raises(ConfigurationError, match="LANGSMITH_API_KEY"):
        Settings.from_env(
            {
                "OPENAI_API_KEY": "openai-secret",
                "GEMINI_API_KEY": "gemini-secret",
                "LANGSMITH_TRACING": "true",
            }
        )


def test_langsmith_defaults_to_disabled_named_project() -> None:
    settings = Settings.from_env(
        {
            "OPENAI_API_KEY": "openai-secret",
            "GEMINI_API_KEY": "gemini-secret",
        }
    )

    assert settings.langsmith_tracing is False
    assert settings.langsmith_api_key is None
    assert settings.langsmith_project == "sage-v2"


@pytest.mark.parametrize(
    "name",
    [
        "SOLVER_MODEL",
        "REVIEWER_MODEL",
    ],
)
def test_settings_rejects_empty_model_names(name: str) -> None:
    with pytest.raises(ConfigurationError, match="Invalid Sage configuration"):
        Settings.from_env(
            {
                "OPENAI_API_KEY": "openai-secret",
                "GEMINI_API_KEY": "gemini-secret",
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
                "GEMINI_API_KEY": "gemini-secret",
                "SAGE_MAX_TURNS": "0",
            }
        )


@pytest.mark.parametrize("value", ["-1", "11", "invalid"])
def test_settings_rejects_invalid_openai_retry_limit(value: str) -> None:
    with pytest.raises(ConfigurationError, match="Invalid Sage configuration"):
        Settings.from_env(
            {
                "OPENAI_API_KEY": "secret",
                "GEMINI_API_KEY": "gemini-secret",
                "OPENAI_MAX_RETRIES": value,
            }
        )
def test_jev_is_opt_in_and_credentials_are_redacted():
    from sage.config import JevSettings
    from sage.errors import ConfigurationError
    import pytest

    assert JevSettings.from_env({}).mode == "off"
    with pytest.raises(ConfigurationError):
        JevSettings.from_env({"SAGE_JEV_NAVIGATION_MODE": "on"})
    for key, value in [("SAGE_JEV_MAX_FOLLOWUP_ACTIONS", "3"), ("SAGE_JEV_TIMEOUT_SECONDS", "3"),
                       ("SAGE_JEV_RUN_WAIT_SECONDS", "9"), ("SAGE_JEV_NAVIGATION_POLICY", "write")]:
        with pytest.raises(ConfigurationError):
            JevSettings.from_env({key: value, "TYPESAFE_API_KEY": "private-key"})
    enabled = JevSettings.from_env({"SAGE_JEV_NAVIGATION_MODE": "shadow", "TYPESAFE_API_KEY": "private-key"})
    assert "private-key" not in str(enabled) + enabled.model_dump_json()
