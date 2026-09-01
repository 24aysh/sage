from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel

from sage.errors import AgentRuntimeError, MemoryPolicyError, RepositoryError
from sage.runtimes.tool_loop import (
    AgentState,
    build_agent_node,
    build_finalize_node,
    build_graph,
    build_invalid_response_node,
    build_turn_limit_node,
    recursion_limit,
    route_after_agent,
)

TEST_INSTRUCTIONS = "Use the provided tools and return a structured result."


class LoopResult(BaseModel):
    summary: str


finalize = build_finalize_node(LoopResult)


class ScriptedModel(Runnable[Any, AIMessage]):
    def __init__(self, responses: Sequence[AIMessage | Exception]) -> None:
        self.responses = list(responses)
        self.inputs: list[list[BaseMessage]] = []
        self.invoke_called = False

    def invoke(self, input: Any, config=None, **kwargs: Any) -> AIMessage:
        del input, config, kwargs
        self.invoke_called = True
        raise AssertionError("The graph must call the model asynchronously.")

    async def ainvoke(self, input: Any, config=None, **kwargs: Any) -> AIMessage:
        del config, kwargs
        self.inputs.append(list(input))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class RecordingRepository:
    def __init__(self, *, read_error: Exception | None = None) -> None:
        self.calls: list[str] = []
        self.read_error = read_error

    def list_tree(self, **kwargs) -> str:
        self.calls.append("list_tree")
        return "tree"

    def search_text(self, **kwargs) -> str:
        self.calls.append("search_text")
        return "matches"

    def read_file(self, **kwargs) -> str:
        self.calls.append("read_file")
        if self.read_error is not None:
            raise self.read_error
        return "1 | content"

    def replace_text(self, **kwargs) -> str:
        self.calls.append("replace_text")
        return "Text replaced."

    def show_diff(self) -> str:
        self.calls.append("show_diff")
        return "Diff:\nchanged"

    def run_command(self, **kwargs):
        self.calls.append("run_command")
        raise AssertionError("run_command was not expected")

    def format_command_result(self, result) -> str:
        raise AssertionError("format_command_result was not expected")


def _call(name: str, call_id: str = "call") -> dict[str, Any]:
    return {"name": name, "args": {}, "id": call_id, "type": "tool_call"}


@pytest.mark.parametrize(
    ("parsed", "tool_calls", "turn", "expected"),
    [
        ({"summary": "done"}, [], 1, "finalize"),
        (None, [_call("read_file")], 1, "tools"),
        (None, [_call("read_file")], 3, "turn_limit"),
        (None, [], 1, "invalid_response"),
        ({"summary": "done"}, [_call("read_file")], 1, "invalid_response"),
        (None, [_call("read_file", "1"), _call("show_diff", "2")], 1, "invalid_response"),
        (None, [_call("unknown")], 1, "invalid_response"),
    ],
)
def test_route_after_agent(
    parsed: object,
    tool_calls: list[dict[str, Any]],
    turn: int,
    expected: str,
) -> None:
    state = _route_state(parsed=parsed, tool_calls=tool_calls, turn=turn)

    route = route_after_agent(
        state,
        tool_names=frozenset({"read_file", "show_diff"}),
        max_turns=3,
    )

    assert route == expected


def test_agent_node_prepends_system_prompt_and_uses_ainvoke() -> None:
    response = _final_message("done")
    model = ScriptedModel([response])
    node = build_agent_node(
        model=model,
        max_turns=3,
        instructions=TEST_INSTRUCTIONS,
        role_name="Test",
    )
    human = HumanMessage(content="issue")

    update = asyncio.run(node({"messages": [human], "model_turns": 0}))

    assert model.invoke_called is False
    assert len(model.inputs) == 1
    assert isinstance(model.inputs[0][0], SystemMessage)
    assert model.inputs[0][0].content == TEST_INSTRUCTIONS
    assert model.inputs[0][1] == human
    assert update["messages"] == [response]
    assert update["model_turns"] == 1
    assert update["pending_output"] == response.additional_kwargs["parsed"]


def test_agent_node_refuses_an_extra_model_turn() -> None:
    model = ScriptedModel([_final_message("unused")])
    node = build_agent_node(
        model=model,
        max_turns=2,
        instructions=TEST_INSTRUCTIONS,
        role_name="Test",
    )

    with pytest.raises(AgentRuntimeError, match=r"turn limit \(2\)"):
        asyncio.run(node({"messages": [], "model_turns": 2}))

    assert model.inputs == []


def test_finalize_validates_dict_and_model_values() -> None:
    from_dict = asyncio.run(
        finalize(
            {
                "messages": [],
                "model_turns": 1,
                "pending_output": {"summary": "dict"},
            }
        )
    )
    model_value = LoopResult(summary="model")
    from_model = asyncio.run(
        finalize(
            {"messages": [], "model_turns": 1, "pending_output": model_value}
        )
    )

    assert from_dict["final_output"] == LoopResult(summary="dict")
    assert from_model["final_output"] == model_value


@pytest.mark.parametrize("pending", [None, {"changed_files_claimed": []}, "bad"])
def test_finalize_rejects_missing_or_invalid_output(pending: object) -> None:
    state: AgentState = {
        "messages": [],
        "model_turns": 1,
        "pending_output": pending,  # type: ignore[typeddict-item]
    }

    with pytest.raises(AgentRuntimeError, match="structured output"):
        asyncio.run(finalize(state))


def test_failure_nodes_raise_clear_application_errors() -> None:
    state = _route_state(parsed=None, tool_calls=[], turn=1)

    with pytest.raises(AgentRuntimeError, match="neither a tool call"):
        asyncio.run(build_invalid_response_node(frozenset({"read_file"}))(state))
    with pytest.raises(AgentRuntimeError, match=r"turn limit \(3\)"):
        asyncio.run(build_turn_limit_node(3)(state))


def test_compiled_graph_renders_expected_mermaid(tmp_path: Path) -> None:
    graph, _model, _repository = _graph(tmp_path, [_final_message("done")])
    drawable = graph.get_graph()
    mermaid = drawable.draw_mermaid()
    print(mermaid)

    assert set(drawable.nodes) == {
        "__start__",
        "agent",
        "tools",
        "finalize",
        "turn_limit",
        "invalid_response",
        "__end__",
    }
    edges = {(edge.source, edge.target) for edge in drawable.edges}
    assert ("__start__", "agent") in edges
    assert ("tools", "agent") in edges
    assert ("finalize", "__end__") in edges
    assert ("turn_limit", "__end__") in edges
    assert ("invalid_response", "__end__") in edges
    assert {
        ("agent", "tools"),
        ("agent", "finalize"),
        ("agent", "turn_limit"),
        ("agent", "invalid_response"),
    }.issubset(edges)
    assert graph.checkpointer is None
    for node in ("agent", "tools", "finalize", "turn_limit", "invalid_response"):
        assert node in mermaid


def test_graph_finalizes_on_first_model_turn(tmp_path: Path) -> None:
    graph, model, repository = _graph(tmp_path, [_final_message("done")])

    result = asyncio.run(graph.ainvoke(_initial_state()))

    assert result == {"final_output": LoopResult(summary="done")}
    assert len(model.inputs) == 1
    assert repository.calls == []


def test_graph_returns_tool_result_before_next_decision(tmp_path: Path) -> None:
    graph, model, repository = _graph(
        tmp_path,
        [_tool_message("read_file", {"path": "app.py"}), _final_message("read")],
    )

    result = asyncio.run(graph.ainvoke(_initial_state()))

    assert result["final_output"].summary == "read"
    assert repository.calls == ["read_file"]
    assert len(model.inputs) == 2
    tool_results = [message for message in model.inputs[1] if isinstance(message, ToolMessage)]
    assert len(tool_results) == 1
    assert tool_results[0].content == "1 | content"


def test_graph_handles_multiple_sequential_tools(tmp_path: Path) -> None:
    graph, model, repository = _graph(
        tmp_path,
        [
            _tool_message("read_file", {"path": "app.py"}, "1"),
            _tool_message("read_file", {"path": "tests.py"}, "2"),
            _final_message("read twice"),
        ],
    )

    result = asyncio.run(graph.ainvoke(_initial_state()))

    assert result["final_output"].summary == "read twice"
    assert repository.calls == ["read_file", "read_file"]
    assert len(model.inputs) == 3


def test_graph_orders_mutation_then_diff(tmp_path: Path) -> None:
    graph, _model, repository = _graph(
        tmp_path,
        [
            _tool_message(
                "replace_text",
                {"path": "app.py", "old_text": "old", "new_text": "new"},
                "1",
            ),
            _tool_message("show_diff", {}, "2"),
            _final_message("changed"),
        ],
    )

    result = asyncio.run(graph.ainvoke(_initial_state()))

    assert result["final_output"].summary == "changed"
    assert repository.calls == ["replace_text", "show_diff"]


@pytest.mark.parametrize(
    "response",
    [
        AIMessage(content="plain text"),
        AIMessage(
            content="",
            tool_calls=[_call("read_file", "1"), _call("show_diff", "2")],
        ),
        AIMessage(
            content="",
            additional_kwargs={"parsed": {"summary": "mixed"}},
            tool_calls=[_call("read_file")],
        ),
        AIMessage(content="", additional_kwargs={"parsed": {"unknown": "field"}}),
    ],
)
def test_graph_rejects_invalid_model_protocol(tmp_path: Path, response: AIMessage) -> None:
    graph, _model, repository = _graph(tmp_path, [response])

    with pytest.raises(AgentRuntimeError):
        asyncio.run(graph.ainvoke(_initial_state()))

    assert repository.calls == []


def test_graph_does_not_execute_tool_requested_on_last_turn(tmp_path: Path) -> None:
    graph, _model, repository = _graph(
        tmp_path,
        [_tool_message("read_file", {"path": "app.py"})],
        max_turns=1,
    )

    with pytest.raises(AgentRuntimeError, match="unexecuted tool call"):
        asyncio.run(graph.ainvoke(_initial_state()))

    assert repository.calls == []


def test_graph_returns_repository_failure_before_next_decision(tmp_path: Path) -> None:
    repository = RecordingRepository(read_error=RepositoryError("read failed"))
    graph, model, repository = _graph(
        tmp_path,
        [
            _tool_message("read_file", {"path": "app.py"}),
            _final_message("blocked"),
        ],
        repository=repository,
    )

    result = asyncio.run(graph.ainvoke(_initial_state()))

    assert result["final_output"].summary == "blocked"
    assert repository.calls == ["read_file"]
    tool_results = [
        message for message in model.inputs[1] if isinstance(message, ToolMessage)
    ]
    assert len(tool_results) == 1
    assert tool_results[0].status == "error"
    assert tool_results[0].content == "Repository tool failed: read failed"


def test_graph_returns_memory_policy_rejection_before_next_decision(
    tmp_path: Path,
) -> None:
    repository = RecordingRepository(
        read_error=MemoryPolicyError(
            "Healthy memory limits tree expansion to depth two."
        )
    )
    graph, model, repository = _graph(
        tmp_path,
        [
            _tool_message("read_file", {"path": "app.py"}),
            _final_message("retried safely"),
        ],
        repository=repository,
    )

    result = asyncio.run(graph.ainvoke(_initial_state()))

    assert result["final_output"].summary == "retried safely"
    assert repository.calls == ["read_file"]
    tool_results = [
        message for message in model.inputs[1] if isinstance(message, ToolMessage)
    ]
    assert len(tool_results) == 1
    assert tool_results[0].status == "error"
    assert tool_results[0].content == (
        "Repository tool failed: Healthy memory limits tree expansion to depth two."
    )


def test_graph_propagates_model_and_unexpected_tool_failures(tmp_path: Path) -> None:
    model_graph, _model, _repository = _graph(tmp_path, [ValueError("model failed")])
    with pytest.raises(ValueError, match="model failed"):
        asyncio.run(model_graph.ainvoke(_initial_state()))

    repository = RecordingRepository(read_error=RuntimeError("unexpected failure"))
    tool_graph, _model, repository = _graph(
        tmp_path,
        [_tool_message("read_file", {"path": "app.py"})],
        repository=repository,
    )
    with pytest.raises(RuntimeError, match="unexpected failure"):
        asyncio.run(tool_graph.ainvoke(_initial_state()))


def _graph(
    tmp_path: Path,
    responses: Sequence[AIMessage | Exception],
    *,
    max_turns: int = 5,
    repository: RecordingRepository | None = None,
):
    model = ScriptedModel(responses)
    repository = repository or RecordingRepository()
    del tmp_path
    tools = _tools(repository)
    return (
        build_graph(
            model=model,
            tools=tools,
            max_turns=max_turns,
            instructions=TEST_INSTRUCTIONS,
            output_schema=LoopResult,
            graph_name="test_tool_loop",
            role_name="Test",
        ),
        model,
        repository,
    )


def _tools(repository: RecordingRepository) -> list[BaseTool]:
    @tool
    async def read_file(path: str) -> str:
        """Read one test file."""

        return repository.read_file(path=path)

    @tool
    async def replace_text(path: str, old_text: str, new_text: str) -> str:
        """Replace text in one test file."""

        return repository.replace_text(
            path=path,
            old_text=old_text,
            new_text=new_text,
        )

    @tool
    async def show_diff() -> str:
        """Show the test diff."""

        return repository.show_diff()

    return [read_file, replace_text, show_diff]


def _initial_state() -> dict[str, object]:
    return {"messages": [HumanMessage(content="issue")], "model_turns": 0}


def _route_state(
    *,
    parsed: object,
    tool_calls: list[dict[str, Any]],
    turn: int,
) -> AgentState:
    additional_kwargs = {"parsed": parsed} if parsed is not None else {}
    return {
        "messages": [
            AIMessage(
                content="",
                additional_kwargs=additional_kwargs,
                tool_calls=tool_calls,
            )
        ],
        "model_turns": turn,
        "pending_output": parsed,  # type: ignore[typeddict-item]
    }


def _tool_message(
    name: str,
    args: dict[str, object],
    call_id: str = "call",
) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}],
    )


def _final_message(summary: str) -> AIMessage:
    return AIMessage(
        content="",
        additional_kwargs={"parsed": {"summary": summary}},
    )


@pytest.mark.parametrize(("turns", "expected"), [(1, 6), (7, 18), (30, 64)])
def test_recursion_limit_is_distinct_from_model_turns(
    turns: int,
    expected: int,
) -> None:
    assert recursion_limit(turns) == expected
