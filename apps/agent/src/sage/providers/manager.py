"""Bounded sequential model-call scheduling for Sage V2."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic, perf_counter

from langchain_core.messages import BaseMessage, HumanMessage
from pydantic import BaseModel

from sage.config import Settings
from sage.domain.usage import AttemptKind, ModelCallRecord, ModelRole, RunProvenance
from sage.errors import AgentRuntimeError
from sage.observability import (
    agent_trace_config,
    log_agent_activity,
    log_agent_finished,
)
from sage.providers.base import ModelProvider, ProviderResult
from sage.providers.errors import ProviderErrorCategory, ProviderInvocationError
from sage.providers.factory import ProviderSet

logger = logging.getLogger(__name__)
UsageWriter = Callable[[RunProvenance], None]


class ModelCallBudgetError(AgentRuntimeError):
    """Raised when a model attempt would violate call or time budget."""


@dataclass(frozen=True, slots=True)
class _RolePolicy:
    primary: ModelProvider
    fallback: ModelProvider | None


class ModelCallManager:
    """The only V2 component authorized to invoke a model provider."""

    def __init__(
        self,
        *,
        settings: Settings,
        providers: ProviderSet,
        usage_writer: UsageWriter | None = None,
        clock: Callable[[], float] = monotonic,
        run_id: str | None = None,
    ) -> None:
        self._settings = settings
        self._policies = {
            ModelRole.PLANNER: _RolePolicy(
                providers.planner, providers.planner_fallback
            ),
            ModelRole.SOLVER: _RolePolicy(providers.solver, None),
            ModelRole.REVIEWER: _RolePolicy(providers.reviewer, None),
        }
        self._usage_writer = usage_writer
        self._clock = clock
        self._run_id = run_id
        self._deadline = clock() + settings.run_deadline_seconds
        self._lock = asyncio.Lock()
        self._records: list[ModelCallRecord] = []
        self._consecutive_failures: dict[str, int] = {}

    @property
    def records(self) -> tuple[ModelCallRecord, ...]:
        return tuple(self._records)

    @property
    def remaining_calls(self) -> int:
        return self._settings.max_model_calls - len(self._records)

    def provenance(
        self,
        *,
        implementation_repairs: int = 0,
        review_repairs: int = 0,
        readiness_context_expansions: int = 0,
        solver_context_expansions: int = 0,
    ) -> RunProvenance:
        return RunProvenance(
            calls=self.records,
            implementation_repairs=implementation_repairs,
            review_repairs=review_repairs,
            readiness_context_expansions=readiness_context_expansions,
            solver_context_expansions=solver_context_expansions,
        )

    async def invoke(
        self,
        *,
        stage: str,
        role: ModelRole,
        messages: list[BaseMessage],
        schema: type[BaseModel],
    ) -> ProviderResult:
        """Invoke one role under retry, fallback, schema, call, and time policy."""

        async with self._lock:
            policy = self._policies[role]
            try:
                return await self._attempt_with_retry(
                    stage=stage,
                    role=role,
                    provider=policy.primary,
                    messages=messages,
                    schema=schema,
                    initial_kind=AttemptKind.PRIMARY,
                )
            except ProviderInvocationError as primary_error:
                if primary_error.category is ProviderErrorCategory.SCHEMA_ERROR:
                    return await self._schema_repair(
                        stage=stage,
                        role=role,
                        provider=policy.primary,
                        messages=messages,
                        schema=schema,
                        error=primary_error,
                    )
                if policy.fallback is None or not _fallback_allowed(role, primary_error):
                    raise
                return await self._single_attempt(
                    stage=stage,
                    role=role,
                    provider=policy.fallback,
                    messages=messages,
                    schema=schema,
                    kind=AttemptKind.FALLBACK,
                    retry_count=0,
                )

    async def _attempt_with_retry(
        self,
        *,
        stage: str,
        role: ModelRole,
        provider: ModelProvider,
        messages: list[BaseMessage],
        schema: type[BaseModel],
        initial_kind: AttemptKind,
    ) -> ProviderResult:
        try:
            return await self._single_attempt(
                stage=stage,
                role=role,
                provider=provider,
                messages=messages,
                schema=schema,
                kind=initial_kind,
                retry_count=0,
            )
        except ProviderInvocationError as error:
            if not self._retry_allowed(error):
                raise
            delay = error.retry_after_seconds or 0
            if delay:
                await asyncio.sleep(delay)
            return await self._single_attempt(
                stage=stage,
                role=role,
                provider=provider,
                messages=messages,
                schema=schema,
                kind=AttemptKind.RETRY,
                retry_count=1,
            )

    async def _schema_repair(
        self,
        *,
        stage: str,
        role: ModelRole,
        provider: ModelProvider,
        messages: list[BaseMessage],
        schema: type[BaseModel],
        error: ProviderInvocationError,
    ) -> ProviderResult:
        validation_hint = ""
        if error.validation_issues:
            validation_hint = (
                " Correct these validation failures: "
                + "; ".join(error.validation_issues)
                + "."
            )
        repair_instruction = HumanMessage(
            content=(
                "Your prior response did not satisfy the required structured schema. "
                "Return only a result that conforms exactly to that schema; do not "
                "change the task or add prose outside the structured result."
                f"{validation_hint}"
            )
        )
        return await self._single_attempt(
            stage=stage,
            role=role,
            provider=provider,
            messages=[*messages, repair_instruction],
            schema=schema,
            kind=AttemptKind.SCHEMA_REPAIR,
            retry_count=0,
        )

    async def _single_attempt(
        self,
        *,
        stage: str,
        role: ModelRole,
        provider: ModelProvider,
        messages: list[BaseMessage],
        schema: type[BaseModel],
        kind: AttemptKind,
        retry_count: int,
    ) -> ProviderResult:
        self._reserve(provider)
        call_number = len(self._records) + 1
        log_agent_activity(
            logger,
            role=role,
            stage=stage,
            attempt=kind,
            provider=provider.provider_name,
            model=provider.model_name,
            call_number=call_number,
            max_calls=self._settings.max_model_calls,
        )
        runnable_config = agent_trace_config(
            run_id=self._run_id,
            role=role,
            stage=stage,
            attempt=kind,
            provider=provider.provider_name,
            model=provider.model_name,
            call_number=call_number,
        )
        started = perf_counter()
        try:
            result = await provider.invoke_structured(
                role=role,
                messages=messages,
                schema=schema,
                timeout_seconds=self._settings.model_request_timeout_seconds,
                runnable_config=runnable_config,
            )
        except ProviderInvocationError as error:
            self._consecutive_failures[provider.provider_name] = (
                self._consecutive_failures.get(provider.provider_name, 0) + 1
            )
            self._append_record(
                ModelCallRecord(
                    call_number=call_number,
                    stage=stage,
                    role=role,
                    attempt_kind=kind,
                    provider=provider.provider_name,
                    model=provider.model_name,
                    latency_ms=round((perf_counter() - started) * 1_000, 2),
                    outcome="error",
                    retry_count=retry_count,
                    error_category=error.category.value,
                    status_code=error.status_code,
                    request_id=error.request_id,
                )
            )
            raise
        self._consecutive_failures[provider.provider_name] = 0
        self._append_record(
            ModelCallRecord(
                call_number=call_number,
                stage=stage,
                role=role,
                attempt_kind=kind,
                provider=result.provider,
                model=result.model,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                cached_tokens=result.cached_tokens,
                latency_ms=result.latency_ms,
                outcome="success",
                retry_count=retry_count,
                request_id=result.request_id,
            )
        )
        return result

    def _reserve(self, provider: ModelProvider) -> None:
        if len(self._records) >= self._settings.max_model_calls:
            raise ModelCallBudgetError("V2 model-call budget is exhausted.")
        remaining = self._deadline - self._clock()
        if remaining <= self._settings.finalization_reserve_seconds:
            raise ModelCallBudgetError("V2 finalization time reserve has been reached.")
        if self._consecutive_failures.get(provider.provider_name, 0) >= 2:
            raise ModelCallBudgetError(
                f"Provider circuit is open for {provider.provider_name}."
            )

    def _retry_allowed(self, error: ProviderInvocationError) -> bool:
        if self._settings.max_rate_limit_retries_per_call == 0:
            return False
        if error.outcome_ambiguous or not error.retryable:
            return False
        if error.category not in {
            ProviderErrorCategory.RATE_LIMITED,
            ProviderErrorCategory.PROVIDER_5XX,
        }:
            return False
        delay = error.retry_after_seconds or 0
        if delay > self._settings.max_retry_after_seconds:
            return False
        return (
            self._deadline - self._clock() - delay
            > self._settings.finalization_reserve_seconds
        )

    def _append_record(self, record: ModelCallRecord) -> None:
        self._records.append(record)
        log_agent_finished(logger, record)
        if self._usage_writer is not None:
            self._usage_writer(self.provenance())


def _fallback_allowed(role: ModelRole, error: ProviderInvocationError) -> bool:
    if error.outcome_ambiguous:
        return False
    if role is ModelRole.PLANNER:
        return error.category in {
            ProviderErrorCategory.PERMISSION_OR_MODEL_ACCESS,
            ProviderErrorCategory.RATE_LIMITED,
            ProviderErrorCategory.PROVIDER_5XX,
            ProviderErrorCategory.TIMEOUT,
        }
    return False
