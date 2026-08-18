"""LangGraph implementation of the provider-neutral V0.1 runtime contract."""

from __future__ import annotations

import asyncio
import logging

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI
from langgraph.errors import GraphRecursionError
from pydantic import ValidationError

from sage.config import Settings
from sage.domain.results import AgentFinalOutput
from sage.domain.runtime import RuntimeContext
from sage.errors import AgentRuntimeError
from sage.runtimes.langgraph.graph import GRAPH_NAME, build_graph
from sage.runtimes.langgraph.prompt import build_initial_message
from sage.runtimes.langgraph.tools import build_tools

logger = logging.getLogger(__name__)


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
