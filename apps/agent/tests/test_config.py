import pytest

from sage.config import (
    DEFAULT_V2_REVIEWER_MODEL,
    DEFAULT_V2_SOLVER_MODEL,
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
            "SAGE_SANDBOX_IMAGE": "custom:v0",
            "SAGE_COMMAND_TIMEOUT_SECONDS": "20",
            "SAGE_MAX_TOOL_OUTPUT_CHARS": "2000",
            "LANGSMITH_TRACING": "true",
            "LANGSMITH_API_KEY": "langsmith-secret",
            "LANGSMITH_PROJECT": "sage-test",
            "LANGSMITH_WORKSPACE_ID": "workspace-123",
            "SAGE_RESEARCH_ENABLED": "true",
            "SAGE_WEB_SEARCH_PROVIDER": "tavily",
            "SAGE_WEB_SEARCH_API_KEY": "search-secret",
            "SAGE_RESEARCH_TIMEOUT_SECONDS": "20",
            "SAGE_RESEARCH_MAX_RESULT_CHARS": "8000",
            "SAGE_RESEARCH_ALLOWED_DOMAINS": "docs.example.com,example.org",
            "SAGE_OFFICIAL_DOCUMENTATION_DOMAINS": "docs.example.com",
        }
    )

    assert settings.openai_api_key == "secret"
    assert settings.openai_max_retries == 4
    assert settings.max_turns == 12
    assert str(settings.runs_dir) == "/tmp/test-runs"
    assert settings.sandbox_image == "custom:v0"
    assert settings.command_timeout_seconds == 20
    assert settings.max_tool_output_chars == 2_000
    assert settings.langsmith_tracing is True
    assert settings.langsmith_project == "sage-test"
    assert settings.langsmith_workspace_id == "workspace-123"
    assert settings.web_search_provider == "tavily"
    assert settings.research_timeout_seconds == 20
    assert settings.research_max_result_chars == 8_000
    assert settings.research_allowed_domains == (
        "docs.example.com",
        "example.org",
    )
    assert "langsmith-secret" not in repr(settings)
    assert "search-secret" not in repr(settings)


def test_settings_uses_v2_and_documented_models_by_default() -> None:
    settings = Settings.from_env(
        {"OPENAI_API_KEY": "secret", "GEMINI_API_KEY": "gemini-secret"}
    )

    assert settings.runtime == "v2"
    assert settings.v2_solver_model == DEFAULT_V2_SOLVER_MODEL == "gpt-5.4-mini"
    assert settings.v2_reviewer_model == DEFAULT_V2_REVIEWER_MODEL == "gemini-3.5-flash"


def test_v2_settings_require_locked_profile_credentials_and_acknowledgement() -> None:
    settings = Settings.from_env(
        {
            "SAGE_RUNTIME": "v2",
            "SAGE_MODEL_PROFILE": "constrained-cross-provider",
            "SAGE_GOOGLE_MODEL_CONTEXT_APPROVED": "true",
            "GEMINI_API_KEY": "gemini-secret",
            "OPENAI_API_KEY": "openai-secret",
            "SAGE_V2_SOLVER_MODEL": "custom-solver",
            "SAGE_V2_REVIEWER_MODEL": "custom-reviewer",
            "SAGE_VERIFICATION_COMMANDS_JSON": (
                '[{"id":"focused","command":"pytest -q tests/test_app.py",'
                '"required":true,"timeout_seconds":20}]'
            ),
        }
    )

    assert settings.runtime == "v2"
    assert settings.v2_solver_model == "custom-solver"
    assert settings.v2_reviewer_model == "custom-reviewer"
    assert settings.verification_commands[0].check_id == "focused"
    assert "secret" not in repr(settings)


def test_v2_settings_use_documented_default_models() -> None:
    settings = Settings.from_env(
        {
            "SAGE_RUNTIME": "v2",
            "GEMINI_API_KEY": "gemini-secret",
            "OPENAI_API_KEY": "openai-secret",
        }
    )

    assert settings.google_model_context_approved is True
    assert settings.v2_solver_model == DEFAULT_V2_SOLVER_MODEL == "gpt-5.4-mini"
    assert settings.v2_reviewer_model == DEFAULT_V2_REVIEWER_MODEL == "gemini-3.5-flash"
    assert settings.research_enabled is True


def test_selected_research_provider_requires_its_secret() -> None:
    with pytest.raises(ConfigurationError, match="SAGE_WEB_SEARCH_API_KEY"):
        Settings.from_env(
            {
                "OPENAI_API_KEY": "openai-secret",
                "GEMINI_API_KEY": "gemini-secret",
                "SAGE_WEB_SEARCH_PROVIDER": "tavily",
            }
        )


@pytest.mark.parametrize("domain", ["localhost", "127.0.0.1", "bad domain"])
def test_research_domains_must_be_public_hostnames(domain: str) -> None:
    with pytest.raises(ConfigurationError, match="public hostnames"):
        Settings.from_env(
            {
                "OPENAI_API_KEY": "openai-secret",
                "GEMINI_API_KEY": "gemini-secret",
                "SAGE_RESEARCH_ALLOWED_DOMAINS": domain,
            }
        )


def test_v2_settings_require_gemini_credentials() -> None:
    with pytest.raises(ConfigurationError, match="GEMINI_API_KEY"):
        Settings.from_env(
            {
                "SAGE_RUNTIME": "v2",
                "OPENAI_API_KEY": "openai-secret",
            }
        )


def test_v2_settings_respect_explicit_google_context_rejection() -> None:
    with pytest.raises(ConfigurationError, match="CONTEXT_APPROVED"):
        Settings.from_env(
            {
                "SAGE_RUNTIME": "v2",
                "SAGE_GOOGLE_MODEL_CONTEXT_APPROVED": "false",
                "GEMINI_API_KEY": "gemini-secret",
                "OPENAI_API_KEY": "openai-secret",
            }
        )


@pytest.mark.parametrize("runtime", ["v1", "v2-prototype", "unknown"])
def test_removed_or_unknown_runtime_is_rejected(runtime: str) -> None:
    with pytest.raises(ConfigurationError, match="SAGE_RUNTIME"):
        Settings.from_env({"SAGE_RUNTIME": runtime})


def test_blank_runtime_defaults_to_v2() -> None:
    settings = Settings.from_env(
        {
            "SAGE_RUNTIME": " ",
            "OPENAI_API_KEY": "openai-secret",
            "GEMINI_API_KEY": "gemini-secret",
        }
    )

    assert settings.runtime == "v2"


def test_removed_admission_environment_is_ignored() -> None:
    settings = Settings.from_env(
        {
            "OPENAI_API_KEY": "openai-secret",
            "GEMINI_API_KEY": "gemini-secret",
            "SAGE_V2_ADMISSION_ENABLED": "true",
            "SAGE_V2_ADMISSION_MAX_TURNS": "1",
            "SAGE_V2_ADMISSION_CONTEXT_CHARS": "8000",
            "SAGE_MAX_CLARIFICATION_ROUNDS": "1",
        }
    )

    assert settings.runtime == "v2"
    assert not hasattr(settings, "v2_admission_enabled")
    assert not hasattr(settings, "v2_admission_max_turns")
    assert not hasattr(settings, "v2_admission_context_chars")
    assert not hasattr(settings, "max_clarification_rounds")


def test_legacy_openai_model_environment_is_ignored() -> None:
    settings = Settings.from_env(
        {
            "OPENAI_API_KEY": "openai-secret",
            "GEMINI_API_KEY": "gemini-secret",
            "OPENAI_MODEL": "legacy-model",
        }
    )

    assert settings.v2_solver_model == DEFAULT_V2_SOLVER_MODEL
    assert not hasattr(settings, "openai_model")


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
        "SAGE_V2_SOLVER_MODEL",
        "SAGE_V2_REVIEWER_MODEL",
    ],
)
def test_settings_rejects_empty_v2_model_names(name: str) -> None:
    with pytest.raises(ConfigurationError, match="Invalid Sage configuration"):
        Settings.from_env(
            {
                "OPENAI_API_KEY": "openai-secret",
                "GEMINI_API_KEY": "gemini-secret",
                name: " ",
            }
        )


@pytest.mark.parametrize(
    "name",
    [
        "SAGE_V2_PLANNER_MODEL",
        "SAGE_V2_PLANNER_FALLBACK_MODEL",
        "SAGE_MAX_MODEL_CALLS",
        "SAGE_MAX_REVIEW_REPAIRS",
    ],
)
def test_v2_rejects_obsolete_patch_first_configuration(name: str) -> None:
    with pytest.raises(ConfigurationError, match="Obsolete patch-first"):
        Settings.from_env(
            {
                "SAGE_RUNTIME": "v2",
                "OPENAI_API_KEY": "openai-secret",
                "GEMINI_API_KEY": "gemini-secret",
                name: "1",
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
