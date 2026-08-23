"""Safe provider-neutral model error taxonomy."""

from __future__ import annotations

from enum import StrEnum


class ProviderErrorCategory(StrEnum):
    AUTHENTICATION = "authentication"
    PERMISSION_OR_MODEL_ACCESS = "permission_or_model_access"
    QUOTA_EXHAUSTED = "quota_exhausted"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    PROVIDER_5XX = "provider_5xx"
    INVALID_RESPONSE = "invalid_response"
    SCHEMA_ERROR = "schema_error"
    CONTEXT_TOO_LARGE = "context_too_large"
    AMBIGUOUS_OUTCOME = "ambiguous_outcome"


class ProviderInvocationError(Exception):
    """Bounded normalized provider error without raw provider content."""

    def __init__(
        self,
        category: ProviderErrorCategory,
        *,
        provider: str,
        model: str,
        status_code: int | None = None,
        retry_after_seconds: float | None = None,
        request_id: str | None = None,
        retryable: bool = False,
        outcome_ambiguous: bool = False,
    ) -> None:
        super().__init__(f"{provider}/{model} request failed: {category.value}")
        self.category = category
        self.provider = provider
        self.model = model
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds
        self.request_id = request_id[:200] if request_id else None
        self.retryable = retryable
        self.outcome_ambiguous = outcome_ambiguous
