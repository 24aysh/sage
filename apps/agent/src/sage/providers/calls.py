"""Sequential model scheduling, deadlines, retries, and usage accounting."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from time import monotonic, perf_counter

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from pydantic import BaseModel

from sage.config import Settings
from sage.domain.usage import (
    AgentToolCallRecord,
    AttemptKind,
    ModelCallRecord,
    ModelRole,
    RunProvenance,
)
from sage.errors import AgentRuntimeError
from sage.observability import agent_trace_config, log_agent_activity, log_agent_finished
from sage.providers.base import ModelProvider, ProviderResult
from sage.providers.errors import ProviderErrorCategory, ProviderInvocationError

logger = logging.getLogger(__name__)
UsageWriter = Callable[[RunProvenance], None]


class FinalizationReserveError(AgentRuntimeError):
    """Raised when another model attempt would consume finalization time."""


class ModelCalls:
    """Serialize model attempts, retain deadlines, and record every call."""

    def __init__(
        self,
        *,
        settings: Settings,
        reviewer: ModelProvider,
        usage_writer: UsageWriter | None = None,
        clock: Callable[[], float] = monotonic,
        run_id: str | None = None,
    ) -> None:
        self._settings = settings
        self._reviewer = reviewer
        self._usage_writer = usage_writer
        self._clock = clock
        self._run_id = run_id
        self._deadline = clock() + settings.run_deadline_seconds
        self._lock = asyncio.Lock()
        self._records: list[ModelCallRecord] = []
        self._tool_calls: list[AgentToolCallRecord] = []
        self._consecutive_failures: dict[str, int] = {}
        self.solver_sessions = 0
        self.review_cycles = 0

    @property
    def records(self) -> tuple[ModelCallRecord, ...]:
        return tuple(self._records)

    def has_time_for_model_call(self) -> bool:
        return (
            self._deadline - self._clock()
            > self._settings.finalization_reserve_seconds
        )

    def provenance(self) -> RunProvenance:
        return RunProvenance(
            calls=self.records,
            tool_calls=tuple(self._tool_calls),
            solver_sessions=self.solver_sessions,
            review_cycles=self.review_cycles,
        )

    def start_solver_session(self) -> None:
        """Record one initial or repair Solver tool-loop session."""

        self.solver_sessions += 1
        self._persist()

    def start_coding_call(self, *, role: ModelRole, stage: str) -> int:
        if role is not ModelRole.SOLVER:
            raise ValueError("Coding calls require the Solver role.")
        self._reserve("openai")
        call_number = len(self._records) + 1
        log_agent_activity(
            logger,
            role=role,
            stage=stage,
            attempt=AttemptKind.PRIMARY,
            provider="openai",
            model=self._settings.solver_model,
            call_number=call_number,
        )
        return call_number

    def finish_coding_call(
        self,
        *,
        role: ModelRole,
        stage: str,
        call_number: int,
        message: AIMessage,
        latency_ms: float,
    ) -> None:
        usage = message.usage_metadata or {}
        input_details = usage.get("input_token_details") or {}
        for item in message.tool_calls:
            name = str(item.get("name") or "unknown").strip()[:100] or "unknown"
            self._tool_calls.append(
                AgentToolCallRecord(
                    call_number=len(self._tool_calls) + 1,
                    model_call_number=call_number,
                    stage=stage,
                    role=role,
                    tool_name=name,
                )
            )
        self._append_record(
            ModelCallRecord(
                call_number=call_number,
                stage=stage,
                role=role,
                attempt_kind=AttemptKind.PRIMARY,
                provider="openai",
                model=self._settings.solver_model,
                input_tokens=_optional_int(usage.get("input_tokens")),
                output_tokens=_optional_int(usage.get("output_tokens")),
                cached_tokens=_optional_int(input_details.get("cache_read")),
                latency_ms=latency_ms,
                outcome="success",
                request_id=_request_id(message),
            )
        )

    def fail_coding_call(
        self,
        *,
        role: ModelRole,
        stage: str,
        call_number: int,
        error: BaseException,
        latency_ms: float,
    ) -> None:
        """Record a failed Solver turn without persisting provider payloads."""

        self._append_record(
            ModelCallRecord(
                call_number=call_number,
                stage=stage,
                role=role,
                attempt_kind=AttemptKind.PRIMARY,
                provider="openai",
                model=self._settings.solver_model,
                latency_ms=latency_ms,
                outcome="error",
                error_category=type(error).__name__[:80],
            )
        )

    async def invoke_reviewer(
        self,
        *,
        stage: str,
        messages: list[BaseMessage],
        schema: type[BaseModel],
    ) -> ProviderResult:
        """Invoke the structured Reviewer with bounded retry and schema repair."""

        async with self._lock:
            self.review_cycles += 1
            try:
                return await self._attempt_with_retry(
                    stage=stage,
                    messages=messages,
                    schema=schema,
                    kind=AttemptKind.PRIMARY,
                )
            except ProviderInvocationError as error:
                if error.category is not ProviderErrorCategory.SCHEMA_ERROR:
                    raise
                validation_hint = ""
                if error.validation_issues:
                    validation_hint = " Correct: " + "; ".join(error.validation_issues)
                instruction = HumanMessage(
                    content=(
                        "Return only a result conforming exactly to the required "
                        f"structured schema.{validation_hint}"
                    )
                )
                return await self._single_attempt(
                    stage=stage,
                    messages=[*messages, instruction],
                    schema=schema,
                    kind=AttemptKind.SCHEMA_REPAIR,
                    retry_count=0,
                )

    async def _attempt_with_retry(
        self,
        *,
        stage: str,
        messages: list[BaseMessage],
        schema: type[BaseModel],
        kind: AttemptKind,
    ) -> ProviderResult:
        try:
            return await self._single_attempt(
                stage=stage,
                messages=messages,
                schema=schema,
                kind=kind,
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
                messages=messages,
                schema=schema,
                kind=AttemptKind.RETRY,
                retry_count=1,
            )

    async def _single_attempt(
        self,
        *,
        stage: str,
        messages: list[BaseMessage],
        schema: type[BaseModel],
        kind: AttemptKind,
        retry_count: int,
    ) -> ProviderResult:
        provider = self._reviewer
        self._reserve(provider.provider_name)
        call_number = len(self._records) + 1
        log_agent_activity(
            logger,
            role=ModelRole.REVIEWER,
            stage=stage,
            attempt=kind,
            provider=provider.provider_name,
            model=provider.model_name,
            call_number=call_number,
        )
        config = agent_trace_config(
            run_id=self._run_id,
            role=ModelRole.REVIEWER,
            stage=stage,
            attempt=kind,
            provider=provider.provider_name,
            model=provider.model_name,
            call_number=call_number,
        )
        started = perf_counter()
        try:
            result = await provider.invoke_structured(
                role=ModelRole.REVIEWER,
                messages=messages,
                schema=schema,
                timeout_seconds=self._settings.model_request_timeout_seconds,
                runnable_config=config,
            )
        except ProviderInvocationError as error:
            self._consecutive_failures[provider.provider_name] = (
                self._consecutive_failures.get(provider.provider_name, 0) + 1
            )
            self._append_record(
                ModelCallRecord(
                    call_number=call_number,
                    stage=stage,
                    role=ModelRole.REVIEWER,
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
                role=ModelRole.REVIEWER,
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

    def _reserve(self, provider_name: str) -> None:
        if not self.has_time_for_model_call():
            raise FinalizationReserveError("The finalization time reserve was reached.")
        if self._consecutive_failures.get(provider_name, 0) >= 2:
            raise FinalizationReserveError(f"Provider circuit is open for {provider_name}.")

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
        self._persist()

    def _persist(self) -> None:
        if self._usage_writer is not None:
            self._usage_writer(self.provenance())


def _optional_int(value: object) -> int | None:
    return int(value) if value is not None else None


def _request_id(message: AIMessage) -> str | None:
    value = message.response_metadata.get("request_id")
    return str(value)[:200] if value else None
