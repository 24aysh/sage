import pytest

from sage.config import Settings
from sage.errors import ConfigurationError
from sage.providers.factory import build_constrained_provider_set


def test_factory_uses_configurable_google_openai_models() -> None:
    providers = build_constrained_provider_set(
        Settings(
            runtime="v2-prototype",
            openai_api_key="openai-test",
            gemini_api_key="gemini-test",
            google_model_context_approved=True,
            v2_planner_model="planner-model",
            v2_planner_fallback_model="planner-fallback-model",
            v2_solver_model="solver-model",
            v2_reviewer_model="reviewer-model",
        )
    )

    assert (providers.planner.provider_name, providers.planner.model_name) == (
        "google",
        "planner-model",
    )
    assert (providers.solver.provider_name, providers.solver.model_name) == (
        "openai",
        "solver-model",
    )
    assert (providers.reviewer.provider_name, providers.reviewer.model_name) == (
        "google",
        "reviewer-model",
    )
    assert providers.planner_fallback is not None
    assert providers.planner_fallback.model_name == "planner-fallback-model"


def test_factory_refuses_unacknowledged_google_context_use() -> None:
    with pytest.raises(ConfigurationError, match="not acknowledged"):
        build_constrained_provider_set(
            Settings(
                runtime="v2-prototype",
                openai_api_key="openai-test",
                gemini_api_key="gemini-test",
            )
        )
