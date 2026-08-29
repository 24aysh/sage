from __future__ import annotations

import asyncio
from types import SimpleNamespace

from langchain_core.messages import HumanMessage
from pydantic import BaseModel

from sage.domain.usage import ModelRole
from sage.providers.base import LangChainStructuredProvider
from sage.providers.errors import ProviderErrorCategory


class Result(BaseModel):
    value: str


class RecordingRunnable:
    def __init__(self) -> None:
        self.config = None

    async def ainvoke(self, messages, *, config=None):
        del messages
        self.config = config
        return Result(value="done")


class RecordingModel:
    def __init__(self, runnable: RecordingRunnable) -> None:
        self.runnable = runnable

    def with_structured_output(self, schema, *, include_raw):
        assert schema is Result
        assert include_raw is True
        return self.runnable


def test_structured_provider_forwards_named_trace_config() -> None:
    runnable = RecordingRunnable()
    provider = LangChainStructuredProvider(
        model=RecordingModel(runnable),  # type: ignore[arg-type]
        provider_name="test-provider",
        model_name="test-model",
    )
    trace_config = {
        "run_name": "Reviewer",
        "tags": ["sage-agent", "role:reviewer"],
        "metadata": {"sage_run_id": "run-123"},
    }

    result = asyncio.run(
        provider.invoke_structured(
            role=ModelRole.REVIEWER,
            messages=[HumanMessage(content="private context")],
            schema=Result,
            timeout_seconds=30,
            runnable_config=trace_config,
        )
    )

    assert result.parsed == Result(value="done")
    assert runnable.config == trace_config


def test_structured_provider_classifies_nested_rate_limit() -> None:
    provider = LangChainStructuredProvider(
        model=RecordingModel(RecordingRunnable()),  # type: ignore[arg-type]
        provider_name="google",
        model_name="gemini-test",
    )
    provider_error = RuntimeError("private provider response")
    provider_error.code = 429  # type: ignore[attr-defined]
    provider_error.response = SimpleNamespace(  # type: ignore[attr-defined]
        headers={"retry-after": "2"}
    )
    wrapper = RuntimeError("GoogleRateLimitError")
    wrapper.__cause__ = provider_error

    classified = provider.classify_error(wrapper)

    assert classified.category is ProviderErrorCategory.RATE_LIMITED
    assert classified.status_code == 429
    assert classified.retry_after_seconds == 2
    assert classified.retryable is True
