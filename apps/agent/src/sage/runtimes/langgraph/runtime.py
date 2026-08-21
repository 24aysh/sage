"""LangGraph implementation of the provider-neutral V0.1 runtime contract."""

from __future__ import annotations

import asyncio
import logging

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI
from langgraph.errors import GraphRecursionError
from openai import APIStatusError, AuthenticationError, RateLimitError
from pydantic import ValidationError

from sage.config import Settings
from sage.domain.results import AgentFinalOutput
from sage.domain.runtime import RuntimeContext
from sage.errors import (
    AgentRuntimeError,
    ModelAPIError,
    ModelAuthenticationError,
    ModelQuotaError,
    ModelRateLimitError,
)
from sage.runtimes.langgraph.graph import GRAPH_NAME, build_graph
from sage.runtimes.langgraph.prompt import build_initial_message
from sage.runtimes.langgraph.tools import build_tools

logger = logging.getLogger(__name__)

_OPENAI_QUOTA_CODES = frozenset(
    {
        "credit_balance_exhausted",
        "insufficient_quota",
        "organization_spend_limit_exceeded",
        "organization_usage_limit_exceeded",
        "project_spend_limit_exceeded",
    }
)


class LangGraphRuntime:
    """Single-agent runtime with project-owned graph routing and termination."""

    def __init__(
        self,
        settings: Settings,
        model: BaseChatModel | None = None,
    ) -> None:
        self._settings = settings
        self._model = model or ChatOpenAI(
            model=settings.openai_model,
            api_key=settings.openai_api_key,
            max_retries=settings.openai_max_retries,
            use_responses_api=True,
        )

    async def solve(
        self,
        *,
        issue_text: str,
        context: RuntimeContext,
    ) -> AgentFinalOutput:
        """Run one fresh graph and return its validated structured output."""

        logger.info(
            "agent graph started",
            extra={
                "run_id": context.prepared_run.run_id,
                "graph_name": GRAPH_NAME,
                "model": self._settings.openai_model,
                "max_model_turns": self._settings.max_turns,
            },
        )
        logger.info(
            "OpenAI request configuration: model=%r api_key_status=configured "
            "validation=pending",
            self._settings.openai_model,
            extra={"run_id": context.prepared_run.run_id},
        )
        try:
            tools = build_tools(context)
            model_with_tools = self._model.bind_tools(
                tools,
                response_format=AgentFinalOutput,
                parallel_tool_calls=False,
                strict=False,
            )
            graph = build_graph(
                model=model_with_tools,
                tools=tools,
                max_turns=self._settings.max_turns,
            )
            result = await graph.ainvoke(
                {
                    "messages": [
                        build_initial_message(
                            base_sha=context.prepared_run.base_sha,
                            issue_text=issue_text,
                        )
                    ],
                    "model_turns": 0,
                },
                config={"recursion_limit": recursion_limit(self._settings.max_turns)},
            )
            final_output = AgentFinalOutput.model_validate(result.get("final_output"))
        except asyncio.CancelledError:
            raise
        except AuthenticationError as error:
            logger.warning(
                "OpenAI request rejected: model=%r "
                "api_key_status=invalid_or_unauthorized "
                "category=openai_authentication",
                self._settings.openai_model,
                extra={
                    "run_id": context.prepared_run.run_id,
                    "failure_category": "openai_authentication",
                },
            )
            raise ModelAuthenticationError(
                "OpenAI rejected the configured API key or its authorization."
            ) from error
        except RateLimitError as error:
            quota_exhausted = is_openai_quota_error(error)
            category = "openai_quota" if quota_exhausted else "openai_rate_limit"
            reset_headers = openai_rate_limit_reset_headers(error)
            logger.warning(
                "OpenAI request rejected: model=%r "
                "api_key_status=accepted_by_api category=%s retry_after=%r "
                "reset_requests=%r reset_tokens=%r reset_project_tokens=%r",
                self._settings.openai_model,
                category,
                reset_headers["retry_after"],
                reset_headers["requests"],
                reset_headers["tokens"],
                reset_headers["project_tokens"],
                extra={
                    "run_id": context.prepared_run.run_id,
                    "failure_category": category,
                },
            )
            if quota_exhausted:
                raise ModelQuotaError(
                    "OpenAI API credits or configured spend/usage limits are "
                    "exhausted."
                ) from error
            raise ModelRateLimitError(
                "OpenAI request or token rate limits remained active after "
                "bounded retries."
            ) from error
        except APIStatusError as error:
            logger.warning(
                "OpenAI request rejected: model=%r "
                "api_key_status=accepted_by_api category=openai_api "
                "status_code=%s",
                self._settings.openai_model,
                error.status_code,
                extra={
                    "run_id": context.prepared_run.run_id,
                    "failure_category": "openai_api",
                },
            )
            raise ModelAPIError(
                f"OpenAI API rejected the request (HTTP {error.status_code})."
            ) from error
        except AgentRuntimeError:
            logger.warning(
                "agent graph failed",
                extra={
                    "run_id": context.prepared_run.run_id,
                    "failure_category": "protocol",
                },
            )
            raise
        except GraphRecursionError as error:
            logger.warning(
                "agent graph failed",
                extra={
                    "run_id": context.prepared_run.run_id,
                    "failure_category": "recursion_limit",
                },
            )
            raise AgentRuntimeError(
                "LangGraph recursion limit was reached unexpectedly."
            ) from error
        except ValidationError as error:
            raise AgentRuntimeError(
                "LangGraph returned an invalid structured result."
            ) from error
        except Exception as error:
            logger.warning(
                "agent graph failed",
                extra={
                    "run_id": context.prepared_run.run_id,
                    "failure_category": type(error).__name__,
                },
            )
            raise AgentRuntimeError(
                f"LangGraph agent run failed ({type(error).__name__})."
            ) from error

        logger.info(
            "OpenAI request completed: model=%r "
            "api_key_status=accepted_by_api",
            self._settings.openai_model,
            extra={"run_id": context.prepared_run.run_id},
        )
        logger.info(
            "agent graph completed",
            extra={
                "run_id": context.prepared_run.run_id,
                "terminal_category": "structured_output",
            },
        )
        return final_output


def recursion_limit(max_turns: int) -> int:
    """Return a defensive graph-step limit distinct from model-turn semantics."""

    return (2 * max_turns) + 4


def is_openai_quota_error(error: RateLimitError) -> bool:
    """Distinguish non-retryable OpenAI quota failures from temporary 429s."""

    return error.code in _OPENAI_QUOTA_CODES or error.type == "insufficient_quota"


def openai_rate_limit_reset_headers(error: RateLimitError) -> dict[str, str | None]:
    """Return only bounded, non-secret provider reset headers for diagnostics."""

    response = getattr(error, "response", None)
    headers = getattr(response, "headers", {})
    return {
        "retry_after": _bounded_header(headers.get("retry-after")),
        "requests": _bounded_header(headers.get("x-ratelimit-reset-requests")),
        "tokens": _bounded_header(headers.get("x-ratelimit-reset-tokens")),
        "project_tokens": _bounded_header(
            headers.get("x-ratelimit-reset-project-tokens")
        ),
    }


def _bounded_header(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized[:100] or None
