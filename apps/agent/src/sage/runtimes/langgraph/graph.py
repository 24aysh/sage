"""Explicit LangGraph state, nodes, routing, and compiled V0.1 topology."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from time import perf_counter
from typing import Annotated, Any, Literal, NotRequired, TypedDict

from langchain_core.messages import AIMessage, AnyMessage, SystemMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode
from pydantic import ValidationError

from sage.domain.results import AgentFinalOutput
from sage.errors import AgentRuntimeError, RepositoryError
from sage.runtimes.langgraph.prompt import CODING_AGENT_INSTRUCTIONS

logger = logging.getLogger(__name__)

GRAPH_NAME = "sage_v0_1"
Route = Literal["tools", "finalize", "turn_limit", "invalid_response"]


class GraphInput(TypedDict):
    """Input supplied by the runtime adapter for one solve."""

    messages: list[AnyMessage]
    model_turns: int


class AgentState(TypedDict):
    """In-memory state owned by the compiled reasoning graph."""

    messages: Annotated[list[AnyMessage], add_messages]
    model_turns: int
    pending_output: NotRequired[AgentFinalOutput | dict[str, object] | None]
    final_output: NotRequired[AgentFinalOutput]


class GraphOutput(TypedDict):
    """Only the validated provider-neutral result leaves the graph."""

    final_output: AgentFinalOutput


def build_agent_node(
    *,
    model: Runnable[Any, AIMessage],
    max_turns: int,
) -> Callable[[AgentState], Awaitable[dict[str, object]]]:
    """Create the node that performs exactly one asynchronous model decision."""

    async def agent(state: AgentState) -> dict[str, object]:
        current_turns = state["model_turns"]
        if current_turns >= max_turns:
            raise AgentRuntimeError(
                f"Model turn limit ({max_turns}) was reached before a decision."
            )

        started = perf_counter()
        response = await model.ainvoke(
            [SystemMessage(content=CODING_AGENT_INSTRUCTIONS), *state["messages"]]
        )
        if not isinstance(response, AIMessage):
            raise AgentRuntimeError("Model returned a non-AI graph message.")

        turn_number = current_turns + 1
        parsed = response.additional_kwargs.get("parsed")
        tool_name = (
            response.tool_calls[0]["name"] if len(response.tool_calls) == 1 else None
        )
        logger.info(
            "model decision completed",
            extra={
                "turn_number": turn_number,
                "has_structured_output": parsed is not None,
                "tool_call_count": len(response.tool_calls),
                "tool_name": tool_name,
                "duration_ms": round((perf_counter() - started) * 1000, 2),
                "total_tokens": _total_tokens(response),
            },
        )
        return {
            "messages": [response],
            "model_turns": turn_number,
            "pending_output": parsed,
        }

    return agent


def route_after_agent(
    state: AgentState,
    *,
    tool_names: frozenset[str],
    max_turns: int,
) -> Route:
    """Choose the next node from graph state without performing side effects."""

    message = _latest_ai_message(state)
    if message is None:
        return "invalid_response"

    tool_calls = message.tool_calls
    has_output = state.get("pending_output") is not None
    if has_output and tool_calls:
        return "invalid_response"
    if has_output:
        return "finalize"
    if len(tool_calls) != 1:
        return "invalid_response"
    if tool_calls[0]["name"] not in tool_names:
        return "invalid_response"
    if state["model_turns"] >= max_turns:
        return "turn_limit"
    return "tools"


async def finalize(state: AgentState) -> dict[str, AgentFinalOutput]:
    """Validate provider-parsed output into the project-owned result model."""

    pending_output = state.get("pending_output")
    if pending_output is None:
        raise AgentRuntimeError("Agent finished without structured output.")
    try:
        final_output = AgentFinalOutput.model_validate(pending_output)
    except (TypeError, ValidationError) as error:
        raise AgentRuntimeError("Agent returned invalid structured output.") from error
    return {"final_output": final_output}


def build_turn_limit_node(max_turns: int) -> Callable[[AgentState], Awaitable[None]]:
    """Create the terminal failure node for an exhausted model-turn budget."""

    async def turn_limit(state: AgentState) -> None:
        del state
        raise AgentRuntimeError(
            f"Model turn limit ({max_turns}) was reached with an unexecuted tool call."
        )

    return turn_limit


def build_invalid_response_node(
    tool_names: frozenset[str],
) -> Callable[[AgentState], Awaitable[None]]:
    """Create the terminal failure node for graph-protocol violations."""

    async def invalid_response(state: AgentState) -> None:
        raise AgentRuntimeError(_invalid_response_reason(state, tool_names))

    return invalid_response


def build_graph(
    *,
    model: Runnable[Any, AIMessage],
    tools: Sequence[BaseTool],
    max_turns: int,
) -> CompiledStateGraph[AgentState, None, GraphInput, GraphOutput]:
    """Compile a fresh, checkpoint-free V0.1 reasoning graph."""

    if max_turns < 1:
        raise ValueError("max_turns must be at least one.")

    tool_names = frozenset(tool.name for tool in tools)

    async def route(state: AgentState) -> Route:
        return route_after_agent(
            state,
            tool_names=tool_names,
            max_turns=max_turns,
        )

    builder = StateGraph(
        AgentState,
        input_schema=GraphInput,
        output_schema=GraphOutput,
    )
    builder.add_node(
        "agent",
        build_agent_node(model=model, max_turns=max_turns),
    )
    builder.add_node(
        "tools",
        ToolNode(
            tools,
            name="tools",
            handle_tool_errors=_handle_repository_tool_error,
        ),
    )
    builder.add_node("finalize", finalize)
    builder.add_node("turn_limit", build_turn_limit_node(max_turns))
    builder.add_node(
        "invalid_response",
        build_invalid_response_node(tool_names),
    )

    builder.add_edge(START, "agent")
    builder.add_conditional_edges(
        "agent",
        route,
        {
            "tools": "tools",
            "finalize": "finalize",
            "turn_limit": "turn_limit",
            "invalid_response": "invalid_response",
        },
    )
    builder.add_edge("tools", "agent")
    builder.add_edge("finalize", END)
    builder.add_edge("turn_limit", END)
    builder.add_edge("invalid_response", END)
    return builder.compile(name=GRAPH_NAME)


def _handle_repository_tool_error(error: RepositoryError) -> str:
    """Return a safe tool result so the model can correct its next request."""

    return f"Repository tool failed: {error}"


def _latest_ai_message(state: AgentState) -> AIMessage | None:
    messages = state.get("messages", [])
    if not messages or not isinstance(messages[-1], AIMessage):
        return None
    return messages[-1]


def _invalid_response_reason(
    state: AgentState,
    tool_names: frozenset[str],
) -> str:
    message = _latest_ai_message(state)
    if message is None:
        return "Model response did not contain an AI message."

    tool_calls = message.tool_calls
    has_output = state.get("pending_output") is not None
    if has_output and tool_calls:
        return "Model response mixed structured output with a repository tool call."
    if len(tool_calls) > 1:
        return "Model response requested multiple repository tools in one turn."
    if len(tool_calls) == 1 and tool_calls[0]["name"] not in tool_names:
        return f"Model requested an unknown repository tool: {tool_calls[0]['name']}"
    return "Model response contained neither a tool call nor structured output."


def _total_tokens(message: AIMessage) -> int | None:
    usage = message.usage_metadata
    if not usage:
        return None
    total = usage.get("total_tokens")
    return int(total) if total is not None else None
