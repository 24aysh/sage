import pytest

from sage.config import Settings
from sage.errors import ConfigurationError


def test_settings_loads_all_supported_environment_values() -> None:
    settings = Settings.from_env(
        {
            "OPENAI_API_KEY": "secret",
            "OPENAI_MODEL": "test-model",
            "SAGE_MAX_TURNS": "12",
            "SAGE_RUNS_DIR": "/tmp/test-runs",
            "SAGE_SANDBOX_IMAGE": "custom:v0",
            "SAGE_COMMAND_TIMEOUT_SECONDS": "20",
            "SAGE_MAX_TOOL_OUTPUT_CHARS": "2000",
        }
    )

    assert settings.openai_api_key == "secret"
    assert settings.openai_model == "test-model"
    assert settings.max_turns == 12
    assert str(settings.runs_dir) == "/tmp/test-runs"
    assert settings.sandbox_image == "custom:v0"
    assert settings.command_timeout_seconds == 20
    assert settings.max_tool_output_chars == 2_000


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
