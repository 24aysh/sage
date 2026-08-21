from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from langgraph.errors import GraphRecursionError

from sage.config import Settings
from sage.domain.requests import PreparedRun
from sage.domain.results import AgentFinalOutput
from sage.domain.runtime import RuntimeContext
from sage.errors import (
    AgentRuntimeError,
    ModelAPIError,
    ModelAuthenticationError,
    ModelQuotaError,
    ModelRateLimitError,
)
from sage.runtimes.langgraph.graph import GRAPH_NAME
from sage.runtimes.langgraph.runtime import LangGraphRuntime, recursion_limit
from sage.runtimes.langgraph.tools import build_tools


class BindingModel:
    def __init__(self, bound_model: object) -> None:
        self.bound_model = bound_model
        self.tools: list[object] | None = None
        self.options: dict[str, object] | None = None

    def bind_tools(self, tools, **kwargs):
        self.tools = list(tools)
        self.options = kwargs
        return self.bound_model


class FakeGraph:
    def __init__(self, *, result: object = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.input: dict[str, object] | None = None
        self.config: dict[str, object] | None = None

    async def ainvoke(self, input, config=None):
        self.input = input
        self.config = config
        if self.error:
            raise self.error
        return self.result


def test_default_model_uses_explicit_settings(monkeypatch, settings: Settings) -> None:
    captured: dict[str, object] = {}
    sentinel = object()

    def fake_chat_openai(**kwargs):
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(
        "sage.runtimes.langgraph.runtime.ChatOpenAI",
        fake_chat_openai,
    )

    runtime = LangGraphRuntime(settings)

    assert runtime._model is sentinel
    assert captured == {
        "model": settings.openai_model,
        "api_key": settings.openai_api_key,
        "max_retries": settings.openai_max_retries,
        "use_responses_api": True,
    }


def test_locked_chat_openai_builds_responses_request_with_tools_and_output(
    tmp_path: Path,
) -> None:
    settings = Settings(openai_api_key="test", openai_model="gpt-5.4-mini")
    tools = build_tools(_context(tmp_path, settings))
    model = ChatOpenAI(
        model=settings.openai_model,
        api_key=settings.openai_api_key,
        use_responses_api=True,
    )

    bound = model.bind_tools(
        tools,
        response_format=AgentFinalOutput,
        parallel_tool_calls=False,
        strict=False,
    )

    payload = bound.bound._get_request_payload(  # type: ignore[attr-defined]
        [HumanMessage(content="issue")],
        **bound.kwargs,
    )
    assert [schema["name"] for schema in payload["tools"]] == [
        "list_tree",
        "search_text",
        "read_file",
        "apply_patch",
        "show_diff",
        "run_command",
    ]
    assert all(schema["strict"] is False for schema in payload["tools"])
    assert payload["text_format"] is AgentFinalOutput
    assert payload["parallel_tool_calls"] is False
    assert "messages" not in payload
    assert "response_format" not in payload


def test_runtime_binds_tools_and_invokes_graph_with_explicit_limits(
    tmp_path: Path,
    monkeypatch,
    caplog,
) -> None:
    caplog.set_level(logging.INFO, logger="sage.runtimes.langgraph.runtime")
    settings = Settings(
        openai_api_key="must-not-be-logged",
        openai_model="logged-model",
        max_turns=7,
    )
    bound_model = object()
    model = BindingModel(bound_model)
    graph = FakeGraph(result={"final_output": {"summary": "done"}})
    build_arguments: dict[str, object] = {}

    def fake_build_graph(**kwargs):
        build_arguments.update(kwargs)
        return graph

    monkeypatch.setattr(
        "sage.runtimes.langgraph.runtime.build_graph",
        fake_build_graph,
    )
    runtime = LangGraphRuntime(settings, model=model)  # type: ignore[arg-type]
    context = _context(tmp_path, settings)

    result = asyncio.run(runtime.solve(issue_text="Exact issue text.", context=context))

    assert result == AgentFinalOutput(summary="done")
    assert model.options == {
        "response_format": AgentFinalOutput,
        "parallel_tool_calls": False,
        "strict": False,
    }
    assert [tool.name for tool in model.tools or []] == [
        "list_tree",
        "search_text",
        "read_file",
        "apply_patch",
        "show_diff",
        "run_command",
    ]
    assert build_arguments == {
        "model": bound_model,
        "tools": model.tools,
        "max_turns": 7,
    }
    assert graph.config == {"recursion_limit": 18}
    assert graph.input is not None
    assert graph.input["model_turns"] == 0
    initial_message = graph.input["messages"][0]  # type: ignore[index]
    assert isinstance(initial_message, HumanMessage)
    assert context.prepared_run.base_sha in str(initial_message.content)
    assert "Exact issue text." in str(initial_message.content)
    assert "Work only through the provided repository tools." in str(
        initial_message.content
    )
    assert (
        "OpenAI request configuration: model='logged-model' "
        "api_key_status=configured validation=pending" in caplog.text
    )
    assert (
        "OpenAI request completed: model='logged-model' "
        "api_key_status=accepted_by_api" in caplog.text
    )
    assert settings.openai_api_key not in caplog.text


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (GraphRecursionError("too many steps"), "recursion limit"),
        (ValueError("provider payload"), "LangGraph agent run failed"),
    ],
)
def test_runtime_wraps_graph_failures_with_chaining(
    tmp_path: Path,
    monkeypatch,
    error: Exception,
    message: str,
) -> None:
    settings = Settings(openai_api_key="test")
    model = BindingModel(object())
    graph = FakeGraph(error=error)
    monkeypatch.setattr(
        "sage.runtimes.langgraph.runtime.build_graph",
        lambda **kwargs: graph,
    )
    runtime = LangGraphRuntime(settings, model=model)  # type: ignore[arg-type]

    with pytest.raises(AgentRuntimeError, match=message) as raised:
        asyncio.run(runtime.solve(issue_text="issue", context=_context(tmp_path, settings)))

    assert raised.value.__cause__ is error


@pytest.mark.parametrize(
    ("code", "error_type", "expected_error", "message"),
    [
        (
            "credit_balance_exhausted",
            "insufficient_quota",
            ModelQuotaError,
            "credits or configured spend/usage limits",
        ),
        (
            "rate_limit_exceeded",
            "requests",
            ModelRateLimitError,
            "rate limits remained active",
        ),
        (
            None,
            "insufficient_quota",
            ModelQuotaError,
            "credits or configured spend/usage limits",
        ),
    ],
)
def test_runtime_classifies_openai_rate_limit_failures(
    tmp_path: Path,
    monkeypatch,
    code: str | None,
    error_type: str,
    expected_error: type[AgentRuntimeError],
    message: str,
    caplog,
) -> None:
    caplog.set_level(logging.INFO, logger="sage.runtimes.langgraph.runtime")

    class FakeResponse:
        headers = {
            "retry-after": "2",
            "x-ratelimit-reset-requests": "2s",
            "x-ratelimit-reset-tokens": "45s",
            "x-ratelimit-reset-project-tokens": "3s",
        }

    class FakeRateLimitError(Exception):
        def __init__(self, message: str) -> None:
            super().__init__(message)
            self.code = code
            self.type = error_type
            self.response = FakeResponse()

    error = FakeRateLimitError("provider payload must not be surfaced")
    graph = FakeGraph(error=error)
    monkeypatch.setattr(
        "sage.runtimes.langgraph.runtime.RateLimitError",
        FakeRateLimitError,
    )
    monkeypatch.setattr(
        "sage.runtimes.langgraph.runtime.build_graph",
        lambda **kwargs: graph,
    )
    settings = Settings(openai_api_key="test")
    runtime = LangGraphRuntime(
        settings,
        model=BindingModel(object()),  # type: ignore[arg-type]
    )

    with pytest.raises(expected_error, match=message) as raised:
        asyncio.run(
            runtime.solve(issue_text="issue", context=_context(tmp_path, settings))
        )

    assert raised.value.__cause__ is error
    assert "provider payload" not in str(raised.value)
    assert "api_key_status=accepted_by_api" in caplog.text
    assert "retry_after='2'" in caplog.text
    assert "reset_requests='2s'" in caplog.text
    assert "reset_tokens='45s'" in caplog.text
    assert "reset_project_tokens='3s'" in caplog.text


def test_runtime_classifies_openai_authentication_failures(
    tmp_path: Path,
    monkeypatch,
    caplog,
) -> None:
    caplog.set_level(logging.INFO, logger="sage.runtimes.langgraph.runtime")

    class FakeAuthenticationError(Exception):
        pass

    error = FakeAuthenticationError("credential detail must stay private")
    graph = FakeGraph(error=error)
    monkeypatch.setattr(
        "sage.runtimes.langgraph.runtime.AuthenticationError",
        FakeAuthenticationError,
    )
    monkeypatch.setattr(
        "sage.runtimes.langgraph.runtime.build_graph",
        lambda **kwargs: graph,
    )
    settings = Settings(openai_api_key="must-not-be-logged")
    runtime = LangGraphRuntime(
        settings,
        model=BindingModel(object()),  # type: ignore[arg-type]
    )

    with pytest.raises(ModelAuthenticationError, match="configured API key"):
        asyncio.run(
            runtime.solve(issue_text="issue", context=_context(tmp_path, settings))
        )

    assert "api_key_status=invalid_or_unauthorized" in caplog.text
    assert "category=openai_authentication" in caplog.text
    assert settings.openai_api_key not in caplog.text
    assert "credential detail" not in caplog.text


def test_runtime_logs_authenticated_openai_api_rejections(
    tmp_path: Path,
    monkeypatch,
    caplog,
) -> None:
    caplog.set_level(logging.INFO, logger="sage.runtimes.langgraph.runtime")

    class FakeAPIStatusError(Exception):
        status_code = 404

    error = FakeAPIStatusError("provider response must stay private")
    graph = FakeGraph(error=error)
    monkeypatch.setattr(
        "sage.runtimes.langgraph.runtime.APIStatusError",
        FakeAPIStatusError,
    )
    monkeypatch.setattr(
        "sage.runtimes.langgraph.runtime.build_graph",
        lambda **kwargs: graph,
    )
    settings = Settings(openai_api_key="must-not-be-logged")
    runtime = LangGraphRuntime(
        settings,
        model=BindingModel(object()),  # type: ignore[arg-type]
    )

    with pytest.raises(ModelAPIError, match="HTTP 404"):
        asyncio.run(
            runtime.solve(issue_text="issue", context=_context(tmp_path, settings))
        )

    assert "api_key_status=accepted_by_api" in caplog.text
    assert "category=openai_api status_code=404" in caplog.text
    assert settings.openai_api_key not in caplog.text
    assert "provider response" not in caplog.text


def test_runtime_preserves_existing_agent_runtime_errors(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(openai_api_key="test")
    original = AgentRuntimeError("protocol failed")
    graph = FakeGraph(error=original)
    monkeypatch.setattr(
        "sage.runtimes.langgraph.runtime.build_graph",
        lambda **kwargs: graph,
    )
    runtime = LangGraphRuntime(
        settings,
        model=BindingModel(object()),  # type: ignore[arg-type]
    )

    with pytest.raises(AgentRuntimeError) as raised:
        asyncio.run(runtime.solve(issue_text="issue", context=_context(tmp_path, settings)))

    assert raised.value is original


def test_runtime_rejects_invalid_graph_output(tmp_path: Path, monkeypatch) -> None:
    settings = Settings(openai_api_key="test")
    graph = FakeGraph(result={})
    monkeypatch.setattr(
        "sage.runtimes.langgraph.runtime.build_graph",
        lambda **kwargs: graph,
    )
    runtime = LangGraphRuntime(
        settings,
        model=BindingModel(object()),  # type: ignore[arg-type]
    )

    with pytest.raises(AgentRuntimeError, match="invalid structured result") as raised:
        asyncio.run(runtime.solve(issue_text="issue", context=_context(tmp_path, settings)))

    assert raised.value.__cause__ is not None


def test_runtime_propagates_cancellation(tmp_path: Path, monkeypatch) -> None:
    settings = Settings(openai_api_key="test")
    graph = FakeGraph(error=asyncio.CancelledError())
    monkeypatch.setattr(
        "sage.runtimes.langgraph.runtime.build_graph",
        lambda **kwargs: graph,
    )
    runtime = LangGraphRuntime(
        settings,
        model=BindingModel(object()),  # type: ignore[arg-type]
    )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(runtime.solve(issue_text="issue", context=_context(tmp_path, settings)))


@pytest.mark.parametrize(("turns", "expected"), [(1, 6), (7, 18), (30, 64)])
def test_recursion_limit_is_distinct_from_model_turns(turns: int, expected: int) -> None:
    assert recursion_limit(turns) == expected


def test_graph_name_is_stable() -> None:
    assert GRAPH_NAME == "sage_v0_1"


def _context(tmp_path: Path, settings: Settings) -> RuntimeContext:
    return RuntimeContext(
        prepared_run=PreparedRun(
            run_id="run-id",
            source_repo=tmp_path,
            run_dir=tmp_path,
            workspace_dir=tmp_path,
            base_ref="HEAD",
            base_sha="a" * 40,
        ),
        sandbox=object(),
        repository=object(),  # type: ignore[arg-type]
        settings=settings,
    )
