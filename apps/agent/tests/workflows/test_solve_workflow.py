import asyncio
import logging
from pathlib import Path

import pytest

from sage.config import Settings
from sage.domain.memory import (
    MemoryBuildResult,
    MemoryBuildType,
    MemoryRetrievalItem,
    MemoryRetrievalOutcome,
    MemoryRetrievalResult,
    MemoryRetrievalStatus,
)
from sage.domain.solve import PreparedRun, SolveRequest
from sage.domain.solve import AgentFinalOutput, SolveOutcome
from sage.errors import AgentRuntimeError, LegionMemoryBuildError, WorkspaceError
from sage.workflows.solve import solve_issue


class FakeSandbox:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def exec(self, command: str, *, timeout_seconds: int | None = None):
        raise AssertionError("The fake repository should handle Git operations.")

    def stop(self) -> None:
        self.stopped = True


class FakeRepository:
    def get_complete_diff(self) -> str:
        return "diff --git a/app.py b/app.py\n"

    def get_changed_files(self) -> list[str]:
        return ["app.py"]


class EmptyRepository:
    def get_complete_diff(self) -> str:
        return ""

    def get_changed_files(self) -> list[str]:
        return []


class FakeStore:
    def __init__(self) -> None:
        self.initialized = False
        self.persisted = False
        self.memory_artifacts = []

    def initialize(self, **kwargs) -> None:
        self.initialized = True

    def write_result(self, **kwargs) -> None:
        self.persisted = True

    def write_legion_memory(self, value) -> None:
        self.memory_artifacts.append(value)


class SuccessfulEngine:
    async def solve(self, *, issue_text: str, context) -> AgentFinalOutput:
        assert issue_text == "Fix it."
        return AgentFinalOutput(summary="Fixed.")


class FailingEngine:
    async def solve(self, *, issue_text: str, context) -> AgentFinalOutput:
        raise AgentRuntimeError("model failed")


class EnvironmentBlockedEngine:
    async def solve(self, *, issue_text: str, context) -> AgentFinalOutput:
        return AgentFinalOutput(
            summary="The reviewer found an environment blocker.",
            outcome=SolveOutcome.ENVIRONMENT_BLOCKED,
        )


class NoChangeEngine:
    async def solve(self, *, issue_text: str, context) -> AgentFinalOutput:
        return AgentFinalOutput(
            summary="No change is required.",
            outcome=SolveOutcome.NO_CHANGE,
        )


def test_solve_issue_uses_git_results_and_cleans_up(tmp_path: Path, monkeypatch) -> None:
    request, prepared, settings = _run_values(tmp_path)
    monkeypatch.setattr("sage.workflows.solve.prepare_run", lambda *_: prepared)
    sandbox = FakeSandbox()
    repository = FakeRepository()
    store = FakeStore()

    result = asyncio.run(
        solve_issue(
            request,
            SuccessfulEngine(),
            settings,
            sandbox_factory=lambda *_: sandbox,
            repository_factory=lambda *_: repository,
            artifacts=store,
        )
    )

    assert result.changed_files == ["app.py"]
    assert result.diff.startswith("diff --git")
    assert sandbox.started is True
    assert sandbox.stopped is True
    assert store.initialized is True
    assert store.persisted is True


def test_solve_issue_cleans_up_after_runtime_failure(tmp_path: Path, monkeypatch) -> None:
    request, prepared, settings = _run_values(tmp_path)
    monkeypatch.setattr("sage.workflows.solve.prepare_run", lambda *_: prepared)
    sandbox = FakeSandbox()

    with pytest.raises(AgentRuntimeError, match="model failed"):
        asyncio.run(
            solve_issue(
                request,
                FailingEngine(),
                settings,
                sandbox_factory=lambda *_: sandbox,
                repository_factory=lambda *_: FakeRepository(),
                artifacts=FakeStore(),
            )
        )

    assert sandbox.started is True
    assert sandbox.stopped is True


def test_solve_issue_preserves_nonpublishable_candidate_for_diagnostics(
    tmp_path: Path,
    monkeypatch,
) -> None:
    request, prepared, settings = _run_values(tmp_path)
    monkeypatch.setattr("sage.workflows.solve.prepare_run", lambda *_: prepared)
    sandbox = FakeSandbox()

    result = asyncio.run(
        solve_issue(
            request,
            EnvironmentBlockedEngine(),
            settings,
            sandbox_factory=lambda *_: sandbox,
            repository_factory=lambda *_: FakeRepository(),
            artifacts=FakeStore(),
        )
    )

    assert result.outcome is SolveOutcome.ENVIRONMENT_BLOCKED
    assert result.changed_files == ["app.py"]
    assert result.diff.startswith("diff --git")
    assert sandbox.stopped is True


def test_solve_issue_rejects_completed_result_without_candidate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    request, prepared, settings = _run_values(tmp_path)
    monkeypatch.setattr("sage.workflows.solve.prepare_run", lambda *_: prepared)
    sandbox = FakeSandbox()

    with pytest.raises(WorkspaceError, match="authoritative candidate"):
        asyncio.run(
            solve_issue(
                request,
                SuccessfulEngine(),
                settings,
                sandbox_factory=lambda *_: sandbox,
                repository_factory=lambda *_: EmptyRepository(),
                artifacts=FakeStore(),
            )
        )

    assert sandbox.stopped is True


def test_solve_issue_rejects_no_change_result_with_candidate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    request, prepared, settings = _run_values(tmp_path)
    monkeypatch.setattr("sage.workflows.solve.prepare_run", lambda *_: prepared)
    sandbox = FakeSandbox()

    with pytest.raises(WorkspaceError, match="No-change"):
        asyncio.run(
            solve_issue(
                request,
                NoChangeEngine(),
                settings,
                sandbox_factory=lambda *_: sandbox,
                repository_factory=lambda *_: FakeRepository(),
                artifacts=FakeStore(),
            )
        )

    assert sandbox.stopped is True


def test_memory_is_prepared_before_sandbox_and_solver(
    tmp_path: Path,
    monkeypatch,
    caplog,
) -> None:
    request, prepared, settings = _run_values(tmp_path)
    memory_file = tmp_path / "graph.sqlite3"
    request = request.model_copy(update={"memory_file": memory_file})
    events: list[str] = []
    caplog.set_level(logging.INFO, logger="sage.workflows.solve")

    def prepare(*_):
        events.append("workspace")
        return prepared

    class MemoryService:
        def build_or_update_graph_tool(self, **arguments):
            events.append("build")
            assert arguments["repo_root"] == prepared.workspace_dir
            return _memory_build(memory_file, prepared.base_sha)

        def retrieve_issue_context(self, **arguments):
            events.append("retrieve")
            assert arguments["issue_text"] == "Fix it."
            return _memory_retrieval(memory_file, prepared.base_sha)

    class OrderedSandbox(FakeSandbox):
        def start(self) -> None:
            events.append("sandbox")
            super().start()

    class MemoryEngine:
        async def solve(self, *, issue_text: str, context) -> AgentFinalOutput:
            events.append("solver")
            assert context.memory is not None
            assert context.memory.initial_context == "base graph context"
            return AgentFinalOutput(summary="Fixed.")

    monkeypatch.setattr("sage.workflows.solve.prepare_run", prepare)
    store = FakeStore()
    sandbox = OrderedSandbox()

    result = asyncio.run(
        solve_issue(
            request,
            MemoryEngine(),
            settings,
            sandbox_factory=lambda *_: sandbox,
            repository_factory=lambda *_: FakeRepository(),
            artifacts=store,
            memory_service=MemoryService(),  # type: ignore[arg-type]
        )
    )

    assert events == ["workspace", "build", "retrieve", "sandbox", "solver"]
    assert result.memory is not None
    assert result.memory.status is MemoryRetrievalStatus.USED
    assert result.memory.indexed_sha == prepared.base_sha
    assert len(store.memory_artifacts) == 2
    assert store.memory_artifacts[-1].status is MemoryRetrievalStatus.USED
    assert "Legion Memory: graph ready" in caplog.text
    assert "Status: used" in caplog.text
    assert sandbox.stopped is True


def test_no_match_keeps_memory_tools_available_without_prompt_context(
    tmp_path: Path,
    monkeypatch,
) -> None:
    request, prepared, settings = _run_values(tmp_path)
    memory_file = tmp_path / "graph.sqlite3"
    request = request.model_copy(update={"memory_file": memory_file})
    monkeypatch.setattr("sage.workflows.solve.prepare_run", lambda *_: prepared)

    class MemoryService:
        def build_or_update_graph_tool(self, **_):
            return _memory_build(memory_file, prepared.base_sha)

        def retrieve_issue_context(self, **_):
            return _memory_retrieval(
                memory_file,
                prepared.base_sha,
                status=MemoryRetrievalStatus.NO_MATCH,
            )

    class NoMatchEngine:
        async def solve(self, *, issue_text: str, context) -> AgentFinalOutput:
            assert context.memory is not None
            assert context.memory.initial_context is None
            return AgentFinalOutput(summary="Fixed.")

    result = asyncio.run(
        solve_issue(
            request,
            NoMatchEngine(),
            settings,
            sandbox_factory=lambda *_: FakeSandbox(),
            repository_factory=lambda *_: FakeRepository(),
            artifacts=FakeStore(),
            memory_service=MemoryService(),  # type: ignore[arg-type]
        )
    )

    assert result.memory is not None
    assert result.memory.status is MemoryRetrievalStatus.NO_MATCH


def test_memory_build_failure_falls_back_and_unrelated_failure_propagates(
    tmp_path: Path,
    monkeypatch,
) -> None:
    request, prepared, settings = _run_values(tmp_path)
    request = request.model_copy(update={"memory_file": tmp_path / "graph.sqlite3"})
    monkeypatch.setattr("sage.workflows.solve.prepare_run", lambda *_: prepared)

    class ExpectedFailureService:
        def build_or_update_graph_tool(self, **_):
            raise LegionMemoryBuildError("database is unavailable")

    class FallbackEngine:
        async def solve(self, *, issue_text: str, context) -> AgentFinalOutput:
            assert context.memory is None
            return AgentFinalOutput(summary="Fixed.")

    result = asyncio.run(
        solve_issue(
            request,
            FallbackEngine(),
            settings,
            sandbox_factory=lambda *_: FakeSandbox(),
            repository_factory=lambda *_: FakeRepository(),
            artifacts=FakeStore(),
            memory_service=ExpectedFailureService(),  # type: ignore[arg-type]
        )
    )
    assert result.memory is not None
    assert result.memory.status is MemoryRetrievalStatus.UNAVAILABLE
    assert result.memory.failure_category == "LegionMemoryBuildError"

    class MismatchedBaseService:
        def build_or_update_graph_tool(self, **_):
            return _memory_build(tmp_path / "graph.sqlite3", "b" * 40)

    mismatch = asyncio.run(
        solve_issue(
            request,
            FallbackEngine(),
            settings,
            sandbox_factory=lambda *_: FakeSandbox(),
            repository_factory=lambda *_: FakeRepository(),
            artifacts=FakeStore(),
            memory_service=MismatchedBaseService(),  # type: ignore[arg-type]
        )
    )
    assert mismatch.memory is not None
    assert mismatch.memory.status is MemoryRetrievalStatus.UNAVAILABLE

    class DefectiveService:
        def build_or_update_graph_tool(self, **_):
            raise RuntimeError("programming defect")

    with pytest.raises(RuntimeError, match="programming defect"):
        asyncio.run(
            solve_issue(
                request,
                FallbackEngine(),
                settings,
                sandbox_factory=lambda *_: FakeSandbox(),
                repository_factory=lambda *_: FakeRepository(),
                artifacts=FakeStore(),
                memory_service=DefectiveService(),  # type: ignore[arg-type]
            )
        )


def test_memory_session_closes_when_solver_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    request, prepared, settings = _run_values(tmp_path)
    memory_file = tmp_path / "graph.sqlite3"
    request = request.model_copy(update={"memory_file": memory_file})
    monkeypatch.setattr("sage.workflows.solve.prepare_run", lambda *_: prepared)
    captured = []

    class MemoryService:
        def build_or_update_graph_tool(self, **_):
            return _memory_build(memory_file, prepared.base_sha)

        def retrieve_issue_context(self, **_):
            return _memory_retrieval(memory_file, prepared.base_sha)

    class CapturingFailureEngine:
        async def solve(self, *, issue_text: str, context) -> AgentFinalOutput:
            captured.append(context.memory)
            raise AgentRuntimeError("model failed")

    with pytest.raises(AgentRuntimeError, match="model failed"):
        asyncio.run(
            solve_issue(
                request,
                CapturingFailureEngine(),
                settings,
                sandbox_factory=lambda *_: FakeSandbox(),
                repository_factory=lambda *_: FakeRepository(),
                artifacts=FakeStore(),
                memory_service=MemoryService(),  # type: ignore[arg-type]
            )
        )

    assert captured[0].closed is True


def _run_values(tmp_path: Path) -> tuple[SolveRequest, PreparedRun, Settings]:
    issue = tmp_path / "issue.md"
    issue.write_text("Fix it.", encoding="utf-8")
    workspace = tmp_path / "repo"
    workspace.mkdir()
    request = SolveRequest(repo_path=tmp_path, issue_path=issue)
    prepared = PreparedRun(
        run_id="run-id",
        source_repo=tmp_path,
        run_dir=tmp_path,
        workspace_dir=workspace,
        base_ref="HEAD",
        base_sha="a" * 40,
    )
    return request, prepared, Settings(openai_api_key="test", runs_dir=tmp_path)


def _memory_build(memory_file: Path, indexed_sha: str) -> MemoryBuildResult:
    return MemoryBuildResult(
        build_type=MemoryBuildType.FULL,
        memory_file=memory_file,
        repository_id="repository-id",
        indexed_sha=indexed_sha,
        schema_version=1,
        files_indexed=2,
        files_parsed=2,
        files_removed=0,
        total_nodes=4,
        total_edges=3,
        total_flows=0,
        total_communities=1,
        languages=("python",),
        duration_ms=2.0,
    )


def _memory_retrieval(
    memory_file: Path,
    indexed_sha: str,
    *,
    status: MemoryRetrievalStatus = MemoryRetrievalStatus.USED,
) -> MemoryRetrievalResult:
    used = status is MemoryRetrievalStatus.USED
    return MemoryRetrievalResult(
        status=status,
        outcome=(
            MemoryRetrievalOutcome.USEFUL_CONTEXT
            if used
            else MemoryRetrievalOutcome.NO_LEXICAL_CANDIDATES
        ),
        summary="Retrieved memory." if used else "No memory matched.",
        memory_file=memory_file,
        repository_id="repository-id",
        indexed_sha=indexed_sha,
        search_modes=("fts",),
        total_candidates=1 if used else 0,
        returned=1 if used else 0,
        context="base graph context" if used else "",
        context_chars=18 if used else 0,
        items=(
            (
                MemoryRetrievalItem(
                    rank=1,
                    kind="Function",
                    name="helper",
                    qualified_name="app.py::helper",
                    file_path="app.py",
                    line_start=1,
                    line_end=2,
                    language="python",
                    score=10.0,
                ),
            )
            if used
            else ()
        ),
        duration_ms=1.0,
    )
