from typing import cast

import pytest
from openai import RateLimitError

from sage.providers.openai import is_openai_quota_error


class _RateLimit:
    def __init__(self, *, code: str | None, error_type: str) -> None:
        self.code = code
        self.type = error_type


@pytest.mark.parametrize(
    ("code", "error_type", "expected"),
    [
        ("credit_balance_exhausted", "requests", True),
        ("project_spend_limit_exceeded", "requests", True),
        (None, "insufficient_quota", True),
        ("rate_limit_exceeded", "requests", False),
    ],
)
def test_openai_quota_classification(
    code: str | None,
    error_type: str,
    expected: bool,
) -> None:
    error = cast(
        RateLimitError,
        _RateLimit(code=code, error_type=error_type),
    )

    assert is_openai_quota_error(error) is expected
