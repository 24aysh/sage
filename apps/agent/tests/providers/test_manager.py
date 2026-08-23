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
            input_tokens=1,
            output_tokens=1,
            cached_tokens=0,
            latency_ms=1,
        )


def test_model_call_logs_visible_role_activity_without_message_content(caplog) -> None:
    planner = Provider(
        "google",
        "gemini-3.5-flash",
        [Result(value="ready")],
    )
    manager = ModelCallManager(
        settings=_settings(),
        run_id="sage-run-123",
        providers=_providers(planner=planner),
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

    assert "Planner: activity" in caplog.text
    assert "Task: Assess issue readiness and draft the execution plan" in caplog.text
    assert "Stage: intake-planner" in caplog.text
    assert "Model: google/gemini-3.5-flash" in caplog.text
    assert "Planner: finished" in caplog.text
    assert "Status: completed" in caplog.text
    assert "private repository context" not in caplog.text
    trace_config = planner.calls[0]["runnable_config"]
    assert trace_config["run_name"] == "Planner"
    assert trace_config["tags"] == [
        "sage-agent",
        "role:planner",
        "stage:intake-planner",
        "provider:google",
    ]
    assert trace_config["metadata"] == {
        "sage_role": "planner",
        "sage_stage": "intake-planner",
        "sage_attempt": "primary",
        "sage_provider": "google",
        "sage_model": "gemini-3.5-flash",
        "sage_call_number": 1,
        "sage_run_id": "sage-run-123",
    }


def test_reviewer_has_no_fallback() -> None:
    error = ProviderInvocationError(
        ProviderErrorCategory.PERMISSION_OR_MODEL_ACCESS,
        provider="google",
        model="gemini-3.5-flash",
        status_code=403,
        request_id="google-request-123",
    )
    providers = _providers(
        reviewer=Provider("google", "gemini-3.5-flash", [error]),
    )
    manager = ModelCallManager(settings=_settings(), providers=providers)

    with pytest.raises(ProviderInvocationError):
        asyncio.run(
            manager.invoke(
                stage="review",
                role=ModelRole.REVIEWER,
                messages=[],
                schema=Result,
            )
        )

    assert len(manager.records) == 1
    assert manager.records[0].status_code == 403
    assert manager.records[0].request_id == "google-request-123"


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
        model="gemini-3.5-flash",
        validation_issues=(
            "plan.acceptance_contract.2.criterion_id: string_too_long",
        ),
    )
    planner = Provider(
        "google",
        "gemini-3.5-flash",
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
    repair_instruction = planner.calls[1]["messages"][-1].content
    assert "plan.acceptance_contract.2.criterion_id: string_too_long" in str(
        repair_instruction
    )


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
        "gemini-3.5-flash",
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
        google_model_context_approved=True,
    )


def _providers(**overrides) -> ProviderSet:
    values = {
        "planner": Provider("google", "gemini-3.5-flash", []),
        "planner_fallback": Provider("google", "planner-fallback", []),
        "solver": Provider("openai", "gpt-5.4-mini", []),
        "reviewer": Provider("google", "gemini-3.5-flash", []),
    }
    values.update(overrides)
    return ProviderSet(**values)
