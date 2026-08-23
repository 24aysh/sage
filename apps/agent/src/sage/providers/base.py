"""Common structured model adapter implemented over LangChain chat models."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Protocol

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from pydantic import BaseModel, ValidationError

from sage.domain.usage import ModelRole
from sage.providers.errors import ProviderErrorCategory, ProviderInvocationError


@dataclass(frozen=True, slots=True)
class ProviderResult:
    """Normalized structured provider response and usage."""

    parsed: BaseModel
    provider: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    cached_tokens: int | None
    latency_ms: float
    request_id: str | None = None


class ModelProvider(Protocol):
    """Narrow provider interface used by the call manager."""

    provider_name: str
    model_name: str

    async def invoke_structured(
        self,
        *,
        role: ModelRole,
        messages: list[BaseMessage],
        schema: type[BaseModel],
        timeout_seconds: int,
    ) -> ProviderResult: ...


class LangChainStructuredProvider:
    """Shared async structured-output behavior for provider integrations."""

    provider_name: str
    model_name: str

    def __init__(self, *, model: BaseChatModel, provider_name: str, model_name: str) -> None:
        self._model = model
        self.provider_name = provider_name
        self.model_name = model_name

    async def invoke_structured(
        self,
        *,
        role: ModelRole,
        messages: list[BaseMessage],
        schema: type[BaseModel],
        timeout_seconds: int,
    ) -> ProviderResult:
        del role
        started = perf_counter()
        try:
            runnable = self._structured_runnable(schema)
            raw_result = await asyncio.wait_for(
                runnable.ainvoke(messages),
                timeout=timeout_seconds,
            )
            parsed, raw_message, parsing_error = _unpack_structured(raw_result)
            if parsing_error is not None or parsed is None:
                raise ProviderInvocationError(
                    ProviderErrorCategory.SCHEMA_ERROR,
                    provider=self.provider_name,
                    model=self.model_name,
                )
            try:
                validated = schema.model_validate(parsed)
            except ValidationError as error:
                raise ProviderInvocationError(
                    ProviderErrorCategory.SCHEMA_ERROR,
                    provider=self.provider_name,
                    model=self.model_name,
                    validation_issues=_validation_issues(error),
                ) from error
            input_tokens, output_tokens, cached_tokens = _usage(raw_message)
            return ProviderResult(
                parsed=validated,
                provider=self.provider_name,
                model=self.model_name,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_tokens=cached_tokens,
                latency_ms=round((perf_counter() - started) * 1_000, 2),
                request_id=_request_id(raw_message),
            )
        except asyncio.TimeoutError as error:
            raise ProviderInvocationError(
                ProviderErrorCategory.TIMEOUT,
                provider=self.provider_name,
                model=self.model_name,
                retryable=True,
                outcome_ambiguous=True,
            ) from error
        except ProviderInvocationError:
            raise
        except Exception as error:
            raise self.classify_error(error) from error

    def _structured_runnable(self, schema: type[BaseModel]) -> Any:
        return self._model.with_structured_output(schema, include_raw=True)

    def classify_error(self, error: Exception) -> ProviderInvocationError:
        """Classify an integration-specific error without exposing its text."""

        status_code = _status_code(error)
        retry_after = _retry_after(error)
        if status_code in {401}:
            category = ProviderErrorCategory.AUTHENTICATION
        elif status_code in {403, 404}:
            category = ProviderErrorCategory.PERMISSION_OR_MODEL_ACCESS
        elif status_code == 429:
            category = ProviderErrorCategory.RATE_LIMITED
        elif status_code is not None and status_code >= 500:
            category = ProviderErrorCategory.PROVIDER_5XX
        elif status_code == 413:
            category = ProviderErrorCategory.CONTEXT_TOO_LARGE
        else:
            category = ProviderErrorCategory.INVALID_RESPONSE
        return ProviderInvocationError(
            category,
            provider=self.provider_name,
            model=self.model_name,
            status_code=status_code,
            retry_after_seconds=retry_after,
            request_id=_exception_request_id(error),
            retryable=category in {
                ProviderErrorCategory.RATE_LIMITED,
                ProviderErrorCategory.PROVIDER_5XX,
            },
        )


def _unpack_structured(value: object) -> tuple[object | None, AIMessage | None, object | None]:
    if isinstance(value, dict) and {"raw", "parsed", "parsing_error"} & value.keys():
        raw = value.get("raw")
        return (
            value.get("parsed"),
            raw if isinstance(raw, AIMessage) else None,
            value.get("parsing_error"),
        )
    return value, None, None


def _usage(message: AIMessage | None) -> tuple[int | None, int | None, int | None]:
    if message is None or not message.usage_metadata:
        return None, None, None
    usage = message.usage_metadata
    input_details = usage.get("input_token_details") or {}
    return (
        _optional_int(usage.get("input_tokens")),
        _optional_int(usage.get("output_tokens")),
        _optional_int(input_details.get("cache_read")),
    )


def _optional_int(value: object) -> int | None:
    return int(value) if value is not None else None


def _request_id(message: AIMessage | None) -> str | None:
    if message is None:
        return None
    raw = message.response_metadata.get("request_id")
    return str(raw)[:200] if raw else None


def _status_code(error: Exception) -> int | None:
    value = getattr(error, "status_code", None)
    if value is None:
        value = getattr(error, "code", None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _retry_after(error: Exception) -> float | None:
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", {}) or {}
    value = headers.get("retry-after") if hasattr(headers, "get") else None
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, parsed)


def _exception_request_id(error: Exception) -> str | None:
    value = getattr(error, "request_id", None)
    return str(value)[:200] if value else None


def _validation_issues(error: ValidationError) -> tuple[str, ...]:
    issues: list[str] = []
    for issue in error.errors(
        include_url=False,
        include_context=False,
        include_input=False,
    )[:8]:
        location = ".".join(str(part) for part in issue.get("loc", ())) or "result"
        error_type = str(issue.get("type", "validation_error"))
        issues.append(f"{location[:200]}: {error_type[:80]}")
    return tuple(issues)
