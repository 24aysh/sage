import pytest

from sage.config import Settings
from sage.errors import ConfigurationError
from sage.providers.factory import build_constrained_provider_set


def test_factory_builds_only_configurable_reviewer_provider() -> None:
    providers = build_constrained_provider_set(
        Settings(
            runtime="v2-prototype",
            openai_api_key="openai-test",
            gemini_api_key="gemini-test",
            google_model_context_approved=True,
            v2_solver_model="solver-model",
            v2_reviewer_model="reviewer-model",
        )
    )

    assert (providers.reviewer.provider_name, providers.reviewer.model_name) == (
        "google",
        "reviewer-model",
    )
    assert tuple(providers.__dataclass_fields__) == ("reviewer",)


def test_factory_refuses_explicitly_rejected_google_context_use() -> None:
    with pytest.raises(ConfigurationError, match="not acknowledged"):
        build_constrained_provider_set(
            Settings(
                runtime="v2-prototype",
                openai_api_key="openai-test",
                gemini_api_key="gemini-test",
                google_model_context_approved=False,
            )
        )
