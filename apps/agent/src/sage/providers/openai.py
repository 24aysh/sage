"""OpenAI error classification for the coding provider."""

from openai import RateLimitError

_OPENAI_QUOTA_CODES = frozenset(
    {
        "credit_balance_exhausted",
        "insufficient_quota",
        "organization_spend_limit_exceeded",
        "organization_usage_limit_exceeded",
        "project_spend_limit_exceeded",
    }
)


def is_openai_quota_error(error: RateLimitError) -> bool:
    """Distinguish non-retryable quota failures from temporary rate limits."""

    return error.code in _OPENAI_QUOTA_CODES or error.type == "insufficient_quota"
