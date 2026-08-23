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
from sage.providers.factory import ProviderSet
from sage.providers.manager import ModelCallBudgetError, ModelCallManager


class Result(BaseModel):
    value: str


class Provider:
    def __init__(self, name: str, model: str, responses: list[object]) -> None:
        self.provider_name = name
        self.model_name = model
        self.responses = responses

    async def invoke_structured(self, **kwargs):
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return ProviderResult(
            parsed=response,
            provider=self.provider_name,
            model=self.model_name,
            input_tokens=1,
            output_tokens=1,
            cached_tokens=0,
            latency_ms=1,
        )


def test_model_call_logs_visible_role_activity_without_message_content(caplog) -> None:
    manager = ModelCallManager(
        settings=_settings(),
        providers=_providers(
            planner=Provider(
                "google",
                "gemini-3.7-flash",
                [Result(value="ready")],
            )
        ),
    )
    caplog.set_level(logging.INFO, logger="sage.providers.manager")

    asyncio.run(
        manager.invoke(
            stage="intake-planner",
            role=ModelRole.PLANNER,
            messages=[HumanMessage(content="private repository context")],
            schema=Result,
        )
    )

    assert (
        "Planner: started stage=intake-planner call=1 attempt=primary "
        "provider=google model=gemini-3.7-flash"
    ) in caplog.text
    assert (
        "Planner: finished stage=intake-planner call=1 attempt=primary "
        "provider=google model=gemini-3.7-flash outcome=success"
    ) in caplog.text
    assert "private repository context" not in caplog.text


def test_reviewer_uses_recorded_google_fallback() -> None:
    anthropic_error = ProviderInvocationError(
        ProviderErrorCategory.QUOTA_EXHAUSTED,
        provider="anthropic",
        model="claude-haiku-4-5",
    )
    providers = _providers(
        reviewer=Provider("anthropic", "claude-haiku-4-5", [anthropic_error]),
        reviewer_fallback=Provider("google", "gemini-3.5-flash", [Result(value="ok")]),
    )
    manager = ModelCallManager(settings=_settings(), providers=providers)

    result = asyncio.run(
        manager.invoke(stage="review", role=ModelRole.REVIEWER, messages=[], schema=Result)
    )

    assert result.provider == "google"
    assert [record.attempt_kind for record in manager.records] == [
        AttemptKind.PRIMARY,
        AttemptKind.FALLBACK,
    ]


def test_solver_has_no_fallback() -> None:
    error = ProviderInvocationError(
        ProviderErrorCategory.PERMISSION_OR_MODEL_ACCESS,
        provider="openai",
        model="gpt-5.4-mini",
    )
    manager = ModelCallManager(
        settings=_settings(),
        providers=_providers(solver=Provider("openai", "gpt-5.4-mini", [error])),
    )

    with pytest.raises(ProviderInvocationError):
        asyncio.run(
            manager.invoke(stage="solver", role=ModelRole.SOLVER, messages=[], schema=Result)
        )
    assert len(manager.records) == 1


def test_schema_repair_and_retry_attempts_are_counted() -> None:
    schema_error = ProviderInvocationError(
        ProviderErrorCategory.SCHEMA_ERROR,
        provider="google",
        model="gemini-3.7-flash",
    )
    planner = Provider(
        "google",
        "gemini-3.7-flash",
        [schema_error, Result(value="repaired")],
    )
    manager = ModelCallManager(
        settings=_settings(),
        providers=_providers(planner=planner),
    )

    result = asyncio.run(
        manager.invoke(stage="intake", role=ModelRole.PLANNER, messages=[], schema=Result)
    )

    assert result.parsed.value == "repaired"
    assert [record.attempt_kind for record in manager.records] == [
        AttemptKind.PRIMARY,
        AttemptKind.SCHEMA_REPAIR,
    ]


def test_retryable_rate_limit_uses_one_counted_retry() -> None:
    rate_limit = ProviderInvocationError(
        ProviderErrorCategory.RATE_LIMITED,
        provider="openai",
        model="gpt-5.4-mini",
        retry_after_seconds=0,
        retryable=True,
    )
    solver = Provider(
        "openai",
        "gpt-5.4-mini",
        [rate_limit, Result(value="retried")],
    )
    manager = ModelCallManager(
        settings=_settings(),
        providers=_providers(solver=solver),
    )

    result = asyncio.run(
        manager.invoke(stage="solver", role=ModelRole.SOLVER, messages=[], schema=Result)
    )

    assert result.parsed.value == "retried"
    assert [record.attempt_kind for record in manager.records] == [
        AttemptKind.PRIMARY,
        AttemptKind.RETRY,
    ]


def test_six_attempt_budget_is_hard() -> None:
    provider = Provider(
        "google",
        "gemini-3.7-flash",
        [Result(value=str(index)) for index in range(6)],
    )
    providers = _providers(planner=provider)
    manager = ModelCallManager(settings=_settings(), providers=providers)
    for index in range(6):
        result = asyncio.run(
            manager.invoke(
                stage=f"planner-{index}",
                role=ModelRole.PLANNER,
                messages=[],
                schema=Result,
            )
        )
        assert result.parsed.value == str(index)
    with pytest.raises(ModelCallBudgetError):
        asyncio.run(
            manager.invoke(stage="seventh", role=ModelRole.PLANNER, messages=[], schema=Result)
        )


def _settings() -> Settings:
    return Settings(
        runtime="v2-prototype",
        openai_api_key="openai",
        gemini_api_key="gemini",
        anthropic_api_key="anthropic",
        google_model_context_approved=True,
    )


def _providers(**overrides) -> ProviderSet:
    values = {
        "planner": Provider("google", "gemini-3.7-flash", []),
        "planner_fallback": Provider("google", "planner-fallback", []),
        "solver": Provider("openai", "gpt-5.4-mini", []),
        "reviewer": Provider("anthropic", "claude-haiku-4-5", []),
        "reviewer_fallback": Provider("google", "reviewer-fallback", []),
    }
    values.update(overrides)
    return ProviderSet(**values)
