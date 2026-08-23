import pytest

from sage.config import Settings
from sage.errors import ConfigurationError
from sage.providers.factory import build_constrained_provider_set


def test_factory_locks_exact_cross_provider_models() -> None:
    providers = build_constrained_provider_set(
        Settings(
            runtime="v2-prototype",
            openai_api_key="openai-test",
            gemini_api_key="gemini-test",
            anthropic_api_key="anthropic-test",
            google_model_context_approved=True,
        )
    )

    assert (providers.planner.provider_name, providers.planner.model_name) == (
        "google",
        "gemini-3.7-flash",
    )
    assert (providers.solver.provider_name, providers.solver.model_name) == (
        "openai",
        "gpt-5.4-mini",
    )
    assert (providers.reviewer.provider_name, providers.reviewer.model_name) == (
        "anthropic",
        "claude-haiku-4-5",
    )
    assert providers.planner_fallback.model_name == "gemini-3.5-flash-lite"
    assert providers.reviewer_fallback.model_name == "gemini-3.5-flash"


def test_factory_refuses_unacknowledged_google_context_use() -> None:
    with pytest.raises(ConfigurationError, match="not acknowledged"):
        build_constrained_provider_set(
            Settings(
                runtime="v2-prototype",
                openai_api_key="openai-test",
                gemini_api_key="gemini-test",
                anthropic_api_key="anthropic-test",
            )
        )
