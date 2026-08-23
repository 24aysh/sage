import pytest

from sage.config import DEFAULT_OPENAI_MODEL, Settings
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
            "ANTHROPIC_API_KEY": "anthropic-secret",
            "SAGE_VERIFICATION_COMMANDS_JSON": (
                '[{"id":"focused","command":"pytest -q tests/test_app.py",'
                '"required":true,"timeout_seconds":20}]'
            ),
        }
    )

    assert settings.runtime == "v2-prototype"
    assert settings.verification_commands[0].check_id == "focused"
    assert "secret" not in repr(settings)


@pytest.mark.parametrize(
    ("missing", "message"),
    [
        ("GEMINI_API_KEY", "GEMINI_API_KEY"),
        ("ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"),
        ("SAGE_GOOGLE_MODEL_CONTEXT_APPROVED", "CONTEXT_APPROVED"),
    ],
)
def test_v2_settings_reject_missing_profile_requirements(
    missing: str,
    message: str,
) -> None:
    values = {
        "SAGE_RUNTIME": "v2-prototype",
        "SAGE_GOOGLE_MODEL_CONTEXT_APPROVED": "true",
        "GEMINI_API_KEY": "gemini-secret",
        "OPENAI_API_KEY": "openai-secret",
        "ANTHROPIC_API_KEY": "anthropic-secret",
    }
    values.pop(missing)

    with pytest.raises(ConfigurationError, match=message):
        Settings.from_env(values)


def test_v1_does_not_require_v2_credentials() -> None:
    settings = Settings.from_env({"OPENAI_API_KEY": "openai-secret"})

    assert settings.runtime == "v1"
    assert settings.gemini_api_key is None
    assert settings.anthropic_api_key is None


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
