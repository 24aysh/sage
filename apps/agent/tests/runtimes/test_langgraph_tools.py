import asyncio
from pathlib import Path

import pytest

from sage.config import Settings
from sage.domain.requests import PreparedRun
from sage.domain.runtime import RuntimeContext
from sage.errors import RepositoryError
from sage.runtimes.langgraph.tools import build_tools
from sage.sandbox.base import CommandResult


class RecordingRepository:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def list_tree(self, **kwargs) -> str:
        self.calls.append(("list_tree", kwargs))
        return "tree result"

    def search_text(self, **kwargs) -> str:
        self.calls.append(("search_text", kwargs))
        return "search result"

    def read_file(self, **kwargs) -> str:
        self.calls.append(("read_file", kwargs))
        return "file result"

    def apply_patch(self, **kwargs) -> str:
        self.calls.append(("apply_patch", kwargs))
        return "patch result"

    def show_diff(self) -> str:
        self.calls.append(("show_diff", {}))
        return "diff result"

    def run_command(self, **kwargs) -> CommandResult:
        self.calls.append(("run_command", kwargs))
        return CommandResult(
            command=str(kwargs["command"]),
            exit_code=0,
            stdout="command output",
            stderr="",
        )

    def format_command_result(self, result: CommandResult) -> str:
        self.calls.append(("format_command_result", {"result": result}))
        return "formatted command result"


def test_build_tools_preserves_exact_v0_capability_surface(tmp_path: Path) -> None:
    tools = build_tools(_context(tmp_path, RecordingRepository()))

    assert [tool.name for tool in tools] == [
        "list_tree",
        "search_text",
        "read_file",
        "apply_patch",
        "show_diff",
        "run_command",
    ]
    assert "get_complete_diff" not in {tool.name for tool in tools}
    assert "get_changed_files" not in {tool.name for tool in tools}


def test_build_tools_preserves_optional_argument_defaults(tmp_path: Path) -> None:
    tools = {tool.name: tool for tool in build_tools(_context(tmp_path, RecordingRepository()))}

    list_tree_schema = tools["list_tree"].args_schema.model_json_schema()
    assert list_tree_schema["properties"]["path"]["default"] == "."
    assert list_tree_schema["properties"]["max_depth"]["default"] == 2

    search_schema = tools["search_text"].args_schema.model_json_schema()
    assert search_schema["required"] == ["query"]
    assert search_schema["properties"]["path"]["default"] == "."
    assert search_schema["properties"]["max_results"]["default"] == 50

    read_schema = tools["read_file"].args_schema.model_json_schema()
    assert read_schema["required"] == ["path"]
    assert read_schema["properties"]["start_line"]["default"] == 1
    assert read_schema["properties"]["end_line"]["default"] is None

    command_schema = tools["run_command"].args_schema.model_json_schema()
    assert command_schema["required"] == ["command"]
    assert command_schema["properties"]["timeout_seconds"]["default"] is None


def test_tools_delegate_arguments_and_return_values_unchanged(tmp_path: Path) -> None:
    repository = RecordingRepository()
    tools = {tool.name: tool for tool in build_tools(_context(tmp_path, repository))}

    assert _invoke(tools["list_tree"], {"path": "src", "max_depth": 3}) == "tree result"
    assert _invoke(
        tools["search_text"],
        {"query": "needle", "path": "src", "max_results": 7},
    ) == "search result"
    assert _invoke(
        tools["read_file"],
        {"path": "src/app.py", "start_line": 2, "end_line": 8},
    ) == "file result"
    assert _invoke(tools["apply_patch"], {"patch": "diff --git ..."}) == "patch result"
    assert _invoke(tools["show_diff"], {}) == "diff result"
    assert _invoke(
        tools["run_command"],
        {"command": "python3 -m unittest", "timeout_seconds": 9},
    ) == "formatted command result"

    assert repository.calls[:6] == [
        ("list_tree", {"path": "src", "max_depth": 3}),
        (
            "search_text",
            {"query": "needle", "path": "src", "max_results": 7},
        ),
        (
            "read_file",
            {"path": "src/app.py", "start_line": 2, "end_line": 8},
        ),
        ("apply_patch", {"patch": "diff --git ..."}),
        ("show_diff", {}),
        (
            "run_command",
            {"command": "python3 -m unittest", "timeout_seconds": 9},
        ),
    ]
    assert repository.calls[6][0] == "format_command_result"


def test_repository_exceptions_are_not_swallowed(tmp_path: Path) -> None:
    repository = RecordingRepository()

    def fail(**kwargs) -> str:
        del kwargs
        raise RepositoryError("repository failed")

    repository.list_tree = fail  # type: ignore[method-assign]
    tool = build_tools(_context(tmp_path, repository))[0]

    with pytest.raises(RepositoryError, match="repository failed"):
        _invoke(tool, {})


def _invoke(tool, arguments: dict[str, object]) -> str:
    return asyncio.run(tool.ainvoke(arguments))


def _context(tmp_path: Path, repository: object) -> RuntimeContext:
    prepared = PreparedRun(
        run_id="run-id",
        source_repo=tmp_path,
        run_dir=tmp_path,
        workspace_dir=tmp_path,
        base_ref="HEAD",
        base_sha="a" * 40,
    )
    return RuntimeContext(
        prepared_run=prepared,
        sandbox=object(),
        repository=repository,  # type: ignore[arg-type]
        settings=Settings(openai_api_key="test"),
    )
