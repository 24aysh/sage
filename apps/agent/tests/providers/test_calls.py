from __future__ import annotations

import asyncio
import logging

import pytest
from langchain_core.messages import HumanMessage
from pydantic import BaseModel

from sage.config import Settings
from sage.domain.usage import AttemptKind, ModelRole
from sage.providers.base import ProviderResult
from sage.providers.errors import ProviderErrorCategory, ProviderInvocationError
from sage.providers.calls import ModelCalls


class Result(BaseModel):
    value: str


class Provider:
    provider_name = "google"
    model_name = "reviewer-model"

    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.calls: list[dict] = []

    async def invoke_structured(self, **kwargs):
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return ProviderResult(
            parsed=response,
            provider=self.provider_name,
            model=self.model_name,
            input_tokens=2,
            output_tokens=1,
            cached_tokens=0,
            latency_ms=1,
        )


def test_reviewer_activity_is_named_and_prompt_content_is_not_logged(caplog) -> None:
    provider = Provider([Result(value="pass")])
    manager = ModelCalls(
        settings=_settings(),
        reviewer=provider,
        run_id="run-1",
    )
    caplog.set_level(logging.INFO, logger="sage.providers.calls")

    asyncio.run(
        manager.invoke_reviewer(
            stage="review",
            messages=[HumanMessage(content="private repository content")],
            schema=Result,
        )
    )

    assert "Reviewer: activity" in caplog.text
    assert "Call: 1" in caplog.text
    assert "Reviewer: finished" in caplog.text
    assert "private repository content" not in caplog.text
    assert provider.calls[0]["role"] is ModelRole.REVIEWER
    assert provider.calls[0]["runnable_config"]["run_name"] == "Reviewer"


def test_schema_error_gets_one_bounded_repair() -> None:
    provider = Provider(
        [
            ProviderInvocationError(
                ProviderErrorCategory.SCHEMA_ERROR,
                provider="google",
                model="reviewer-model",
                validation_issues=("verdict: missing",),
            ),
            Result(value="repaired"),
        ]
    )
    manager = ModelCalls(
        settings=_settings(),
        reviewer=provider,
    )

    result = asyncio.run(
        manager.invoke_reviewer(stage="review", messages=[], schema=Result)
    )

    assert result.parsed.value == "repaired"
    assert [record.attempt_kind for record in manager.records] == [
        AttemptKind.PRIMARY,
        AttemptKind.SCHEMA_REPAIR,
    ]
    assert "verdict: missing" in str(provider.calls[1]["messages"][-1].content)


def test_call_accounting_has_no_global_six_call_limit() -> None:
    provider = Provider([Result(value=str(index)) for index in range(8)])
    manager = ModelCalls(
        settings=_settings(),
        reviewer=provider,
    )

    for index in range(8):
        result = asyncio.run(
            manager.invoke_reviewer(
                stage="review",
                messages=[],
                schema=Result,
            )
        )
        assert result.parsed.value == str(index)

    assert len(manager.records) == 8
    assert manager.records[-1].call_number == 8


def test_reviewer_has_no_provider_fallback() -> None:
    error = ProviderInvocationError(
        ProviderErrorCategory.PERMISSION_OR_MODEL_ACCESS,
        provider="google",
        model="reviewer-model",
    )
    manager = ModelCalls(
        settings=_settings(),
        reviewer=Provider([error]),
    )

    with pytest.raises(ProviderInvocationError):
        asyncio.run(
            manager.invoke_reviewer(stage="review", messages=[], schema=Result)
        )
    assert len(manager.records) == 1


def _settings() -> Settings:
    return Settings(
        openai_api_key="openai",
        gemini_api_key="gemini",
    )
