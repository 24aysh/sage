"""Construction of the locked constrained cross-provider profile."""

from dataclasses import dataclass

from sage.config import (
    DEFAULT_V2_PROFILE,
    V2_PLANNER_FALLBACK_MODEL,
    V2_PLANNER_MODEL,
    V2_REVIEWER_FALLBACK_MODEL,
    V2_REVIEWER_MODEL,
    V2_SOLVER_MODEL,
    Settings,
)
from sage.errors import ConfigurationError
from sage.providers.anthropic import AnthropicProvider
from sage.providers.base import ModelProvider
from sage.providers.google import GoogleProvider
from sage.providers.openai import OpenAIProvider


@dataclass(frozen=True, slots=True)
class ProviderSet:
    planner: ModelProvider
    planner_fallback: ModelProvider
    solver: ModelProvider
    reviewer: ModelProvider
    reviewer_fallback: ModelProvider


def build_constrained_provider_set(settings: Settings) -> ProviderSet:
    """Build exactly the provisional constrained profile."""

    if settings.runtime != "v2-prototype":
        raise ConfigurationError("V2 providers require SAGE_RUNTIME=v2-prototype.")
    if settings.model_profile != DEFAULT_V2_PROFILE:
        raise ConfigurationError("Unsupported V2 model profile.")
    if not settings.google_model_context_approved:
        raise ConfigurationError("V2 Google model context use is not acknowledged.")
    if (
        not settings.gemini_api_key
        or not settings.openai_api_key
        or not settings.anthropic_api_key
    ):
        raise ConfigurationError("V2 provider credentials are incomplete.")

    google_key = settings.gemini_api_key
    timeout = settings.model_request_timeout_seconds
    return ProviderSet(
        planner=GoogleProvider(
            api_key=google_key,
            model_name=V2_PLANNER_MODEL,
            timeout_seconds=timeout,
        ),
        planner_fallback=GoogleProvider(
            api_key=google_key,
            model_name=V2_PLANNER_FALLBACK_MODEL,
            timeout_seconds=timeout,
        ),
        solver=OpenAIProvider(
            api_key=settings.openai_api_key,
            model_name=V2_SOLVER_MODEL,
            timeout_seconds=timeout,
        ),
        reviewer=AnthropicProvider(
            api_key=settings.anthropic_api_key,
            model_name=V2_REVIEWER_MODEL,
            timeout_seconds=timeout,
        ),
        reviewer_fallback=GoogleProvider(
            api_key=google_key,
            model_name=V2_REVIEWER_FALLBACK_MODEL,
            timeout_seconds=timeout,
        ),
    )
