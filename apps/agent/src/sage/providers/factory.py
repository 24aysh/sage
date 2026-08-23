"""Construction of the locked constrained cross-provider profile."""

from dataclasses import dataclass

from sage.config import (
    DEFAULT_V2_PROFILE,
    Settings,
)
from sage.errors import ConfigurationError
from sage.providers.base import ModelProvider
from sage.providers.google import GoogleProvider
from sage.providers.openai import OpenAIProvider


@dataclass(frozen=True, slots=True)
class ProviderSet:
    planner: ModelProvider
    planner_fallback: ModelProvider | None
    solver: ModelProvider
    reviewer: ModelProvider


def build_constrained_provider_set(settings: Settings) -> ProviderSet:
    """Build exactly the provisional constrained profile."""

    if settings.runtime != "v2-prototype":
        raise ConfigurationError("V2 providers require SAGE_RUNTIME=v2-prototype.")
    if settings.model_profile != DEFAULT_V2_PROFILE:
        raise ConfigurationError("Unsupported V2 model profile.")
    if not settings.google_model_context_approved:
        raise ConfigurationError("V2 Google model context use is not acknowledged.")
    if not settings.gemini_api_key or not settings.openai_api_key:
        raise ConfigurationError("V2 provider credentials are incomplete.")

    google_key = settings.gemini_api_key
    timeout = settings.model_request_timeout_seconds
    return ProviderSet(
        planner=GoogleProvider(
            api_key=google_key,
            model_name=settings.v2_planner_model,
            timeout_seconds=timeout,
        ),
        planner_fallback=GoogleProvider(
            api_key=google_key,
            model_name=settings.v2_planner_fallback_model,
            timeout_seconds=timeout,
        ),
        solver=OpenAIProvider(
            api_key=settings.openai_api_key,
            model_name=settings.v2_solver_model,
            timeout_seconds=timeout,
        ),
        reviewer=GoogleProvider(
            api_key=google_key,
            model_name=settings.v2_reviewer_model,
            timeout_seconds=timeout,
        ),
    )
