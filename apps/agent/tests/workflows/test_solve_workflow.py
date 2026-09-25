import asyncio
import json
import logging
from pathlib import Path

import pytest

from sage.config import Settings
from sage.artifacts.store import RunArtifacts
from sage.domain.usage import AgentTimingRecord, RunProvenance
from sage.domain.retrieval import (
    IndexBuildResult,
    IndexBuildType,
    RetrievalItem,
    RetrievalOutcome,
    RetrievalResult,
    RetrievalStatus,
)
from sage.domain.solve import PreparedRun, SolveRequest
from sage.domain.solve import AgentFinalOutput, SolveOutcome
from sage.errors import AgentRuntimeError, ArtifactError, RetrievalBuildError, WorkspaceError
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
    def write_workflow_timing(self, duration_ms: float) -> None:
        assert duration_ms >= 0
        self.duration_ms = duration_ms

    def __init__(self) -> None:
        self.initialized = False
        self.persisted = False
        self.retrieval_artifacts = []

    def initialize(self, **kwargs) -> None:
        self.initialized = True

    def write_result(self, **kwargs) -> None:
        self.persisted = True

    def write_retrieval(self, value) -> None:
        self.retrieval_artifacts.append(value)


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


def test_role_instructions_are_snapshotted_before_sandbox_start(tmp_path, monkeypatch):
    request, prepared, settings = _run_values(tmp_path)
    solver_file = prepared.workspace_dir / "sage-solver.md"
    solver_file.write_text("Original solver policy")
    (prepared.workspace_dir / "sage-reviewer.md").write_text("Original reviewer policy")
    monkeypatch.setattr("sage.workflows.solve.prepare_run", lambda *_: prepared)

    class EditingSandbox(FakeSandbox):
        def start(self):
            super().start()
            solver_file.write_text("Changed policy")

    class InspectingEngine(SuccessfulEngine):
        async def solve(self, *, issue_text, context):
            assert context.instructions.solver == "Original solver policy"
            assert context.instructions.reviewer == "Original reviewer policy"
            return await super().solve(issue_text=issue_text, context=context)

    asyncio.run(solve_issue(request, InspectingEngine(), settings,
        sandbox_factory=lambda *_: EditingSandbox(), repository_factory=lambda *_: FakeRepository(),
        artifacts=FakeStore()))


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
    assert result.workflow_duration_ms == store.duration_ms


@pytest.mark.parametrize("no_change", [False, True])
def test_workflow_duration_spans_issue_read_through_cleanup(tmp_path, monkeypatch, no_change):
    from sage.workflows import solve

    request, prepared, settings = _run_values(tmp_path)
    sandbox, store = FakeSandbox(), FakeStore()
    timestamps = []
    read_issue = solve._read_issue

    def clock():
        if timestamps:
            assert sandbox.stopped and store.persisted
            return 75.5
        timestamps.append(10.0)
        return 10.0

    def read(request):
        assert timestamps == [10.0]
        return read_issue(request)

    monkeypatch.setattr(solve, "perf_counter", clock)
    monkeypatch.setattr(solve, "_read_issue", read)
    monkeypatch.setattr(solve, "prepare_run", lambda *_: prepared)
    result = asyncio.run(solve_issue(request, NoChangeEngine() if no_change else SuccessfulEngine(),
        settings, sandbox_factory=lambda *_: sandbox,
        repository_factory=lambda *_: EmptyRepository() if no_change else FakeRepository(), artifacts=store))
    assert result.workflow_duration_ms == store.duration_ms == 65500.0


def test_solve_issue_cleans_up_after_runtime_failure(tmp_path: Path, monkeypatch) -> None:
    request, prepared, settings = _run_values(tmp_path)
    monkeypatch.setattr("sage.workflows.solve.prepare_run", lambda *_: prepared)
    sandbox = FakeSandbox()
    store = FakeStore()

    with pytest.raises(AgentRuntimeError, match="model failed"):
        asyncio.run(
            solve_issue(
                request,
                FailingEngine(),
                settings,
                sandbox_factory=lambda *_: sandbox,
                repository_factory=lambda *_: FakeRepository(),
                artifacts=store,
            )
        )

    assert sandbox.started is True
    assert sandbox.stopped is True
    assert store.duration_ms >= 0


@pytest.mark.parametrize("error_type", [asyncio.CancelledError, KeyboardInterrupt])
@pytest.mark.parametrize("write_failure", [False, True])
def test_interruption_reports_snapshot_before_cleanup_and_propagates(
    tmp_path, monkeypatch, error_type, write_failure,
):
    request, prepared, settings = _run_values(tmp_path)
    monkeypatch.setattr("sage.workflows.solve.prepare_run", lambda *_: prepared)
    tick = [10.0]
    monkeypatch.setattr("sage.workflows.solve.perf_counter", lambda: tick[0])
    store = RunArtifacts(prepared.run_dir)
    usage = RunProvenance(agent_timings=(AgentTimingRecord(role="solver", stage="solver", duration_ms=3000),))

    class InterruptedEngine:
        async def solve(self, **kwargs):
            store.write_usage(usage)
            tick[0] = 15.0
            raise error_type()

    class SlowCleanup(FakeSandbox):
        def stop(self):
            tick[0] += 7.0
            super().stop()

    sandbox, reported = SlowCleanup(), []

    def report(partial):
        assert not sandbox.stopped
        reported.append(partial)

    if write_failure:
        def fail(result):
            raise ArtifactError("cannot write")
        monkeypatch.setattr(store, "write_interrupted", fail)
    with pytest.raises(error_type):
        asyncio.run(solve_issue(request, InterruptedEngine(), settings, artifacts=store,
            sandbox_factory=lambda *_: sandbox, repository_factory=lambda *_: object(), on_interrupted=report))
    assert sandbox.stopped
    partial, = reported
    assert partial.outcome is SolveOutcome.INTERRUPTED
    assert partial.provenance == usage
    assert partial.workflow_duration_ms == 5000
    assert not (prepared.run_dir / "agent-final.json").exists()
    assert json.loads((prepared.run_dir / "workflow-timing.json").read_text())["duration_ms"] == 12000
    if not write_failure:
        saved = json.loads((prepared.run_dir / "interrupted.json").read_text())
        assert saved["outcome"] == "interrupted" and saved["workflow_duration_ms"] == 5000


def test_interruption_before_model_activity_reports_unknown_usage(tmp_path, monkeypatch):
    request, prepared, settings = _run_values(tmp_path)
    monkeypatch.setattr("sage.workflows.solve.prepare_run", lambda *_: prepared)

    class InterruptedSandbox(FakeSandbox):
        def start(self):
            raise asyncio.CancelledError()

    sandbox, reported = InterruptedSandbox(), []
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(solve_issue(request, SuccessfulEngine(), settings,
            sandbox_factory=lambda *_: sandbox, on_interrupted=reported.append))
    assert sandbox.stopped
    assert reported[0].provenance is None


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


def test_retrieval_is_prepared_after_sandbox_start_before_solver(
    tmp_path: Path,
    monkeypatch,
    caplog,
) -> None:
    request, prepared, settings = _run_values(tmp_path)
    index_file = tmp_path / "graph.sqlite3"
    request = request.model_copy(update={"index_file": index_file})
    events: list[str] = []
    caplog.set_level(logging.INFO, logger="sage.workflows.solve")

    def prepare(*_):
        events.append("workspace")
        return prepared

    class RetrievalService:

        def build_or_update_graph_tool(self, **arguments):
            events.append("build")
            assert arguments["repo_root"] == prepared.workspace_dir
            return _retrieval_build(index_file, prepared.base_sha)

        def retrieve_issue_context(self, **arguments):
            events.append("retrieve")
            assert arguments["issue_text"] == "Fix it."
            return _retrieval_retrieval(index_file, prepared.base_sha)

    class OrderedSandbox(FakeSandbox):
        def start(self) -> None:
            events.append("sandbox")
            super().start()

    class RetrievalEngine:
        async def solve(self, *, issue_text: str, context) -> AgentFinalOutput:
            events.append("solver")
            assert context.retrieval is not None
            assert context.retrieval.initial_context == "base graph context"
            return AgentFinalOutput(summary="Fixed.")

    monkeypatch.setattr("sage.workflows.solve.prepare_run", prepare)
    store = FakeStore()
    sandbox = OrderedSandbox()

    result = asyncio.run(
        solve_issue(
            request,
            RetrievalEngine(),
            settings,
            sandbox_factory=lambda *_: sandbox,
            repository_factory=lambda *_: FakeRepository(),
            artifacts=store,
            retrieval_service=RetrievalService(),  # type: ignore[arg-type]
        )
    )

    assert events == ["workspace", "sandbox", "build", "retrieve", "solver"]
    assert result.retrieval is not None
    assert result.retrieval.status is RetrievalStatus.USED
    assert result.retrieval.indexed_sha == prepared.base_sha
    assert result.workflow_duration_ms == store.duration_ms
    assert len(store.retrieval_artifacts) == 2
    assert store.retrieval_artifacts[-1].status is RetrievalStatus.USED
    assert "Repository retrieval: index ready" in caplog.text
    assert "Status: used" in caplog.text
    assert sandbox.stopped is True


def test_no_match_keeps_retrieval_tools_available_without_prompt_context(
    tmp_path: Path,
    monkeypatch,
) -> None:
    request, prepared, settings = _run_values(tmp_path)
    index_file = tmp_path / "graph.sqlite3"
    request = request.model_copy(update={"index_file": index_file})
    monkeypatch.setattr("sage.workflows.solve.prepare_run", lambda *_: prepared)

    class RetrievalService:

        def build_or_update_graph_tool(self, **_):
            return _retrieval_build(index_file, prepared.base_sha)

        def retrieve_issue_context(self, **_):
            return _retrieval_retrieval(
                index_file,
                prepared.base_sha,
                status=RetrievalStatus.NO_MATCH,
            )

    class NoMatchEngine:
        async def solve(self, *, issue_text: str, context) -> AgentFinalOutput:
            assert context.retrieval is not None
            assert context.retrieval.initial_context is None
            return AgentFinalOutput(summary="Fixed.")

    result = asyncio.run(
        solve_issue(
            request,
            NoMatchEngine(),
            settings,
            sandbox_factory=lambda *_: FakeSandbox(),
            repository_factory=lambda *_: FakeRepository(),
            artifacts=FakeStore(),
            retrieval_service=RetrievalService(),  # type: ignore[arg-type]
        )
    )

    assert result.retrieval is not None
    assert result.retrieval.status is RetrievalStatus.NO_MATCH


def test_retrieval_build_failure_falls_back_and_unrelated_failure_propagates(
    tmp_path: Path,
    monkeypatch,
) -> None:
    request, prepared, settings = _run_values(tmp_path)
    request = request.model_copy(update={"index_file": tmp_path / "graph.sqlite3"})
    monkeypatch.setattr("sage.workflows.solve.prepare_run", lambda *_: prepared)

    class ExpectedFailureService:
        def build_or_update_graph_tool(self, **_):
            raise RetrievalBuildError("database is unavailable")

    class FallbackEngine:
        async def solve(self, *, issue_text: str, context) -> AgentFinalOutput:
            assert context.retrieval is None
            return AgentFinalOutput(summary="Fixed.")

    result = asyncio.run(
        solve_issue(
            request,
            FallbackEngine(),
            settings,
            sandbox_factory=lambda *_: FakeSandbox(),
            repository_factory=lambda *_: FakeRepository(),
            artifacts=FakeStore(),
            retrieval_service=ExpectedFailureService(),  # type: ignore[arg-type]
        )
    )
    assert result.retrieval is not None
    assert result.retrieval.status is RetrievalStatus.UNAVAILABLE
    assert result.retrieval.failure_category == "RetrievalBuildError"

    class MismatchedBaseService:
        def build_or_update_graph_tool(self, **_):
            return _retrieval_build(tmp_path / "graph.sqlite3", "b" * 40)

    mismatch = asyncio.run(
        solve_issue(
            request,
            FallbackEngine(),
            settings,
            sandbox_factory=lambda *_: FakeSandbox(),
            repository_factory=lambda *_: FakeRepository(),
            artifacts=FakeStore(),
            retrieval_service=MismatchedBaseService(),  # type: ignore[arg-type]
        )
    )
    assert mismatch.retrieval is not None
    assert mismatch.retrieval.status is RetrievalStatus.UNAVAILABLE

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
                retrieval_service=DefectiveService(),  # type: ignore[arg-type]
            )
        )


@pytest.mark.parametrize("error_type", [AgentRuntimeError, asyncio.CancelledError])
def test_retrieval_session_closes_when_solver_fails(
    tmp_path: Path,
    monkeypatch,
    error_type,
) -> None:
    request, prepared, settings = _run_values(tmp_path)
    index_file = tmp_path / "graph.sqlite3"
    request = request.model_copy(update={"index_file": index_file})
    monkeypatch.setattr("sage.workflows.solve.prepare_run", lambda *_: prepared)
    captured = []

    class RetrievalService:

        def build_or_update_graph_tool(self, **_):
            return _retrieval_build(index_file, prepared.base_sha)

        def retrieve_issue_context(self, **_):
            return _retrieval_retrieval(index_file, prepared.base_sha)

    class CapturingFailureEngine:
        async def solve(self, *, issue_text: str, context) -> AgentFinalOutput:
            captured.append(context.retrieval)
            raise error_type("model failed")

    with pytest.raises(error_type, match="model failed"):
        asyncio.run(
            solve_issue(
                request,
                CapturingFailureEngine(),
                settings,
                sandbox_factory=lambda *_: FakeSandbox(),
                repository_factory=lambda *_: FakeRepository(),
                artifacts=RunArtifacts(prepared.run_dir),
                retrieval_service=RetrievalService(),  # type: ignore[arg-type]
            )
        )

    assert captured[0].closed is True


@pytest.mark.parametrize("with_memory", [False, True])
def test_preflight_failure_stops_both_modes_before_retrieval_or_model(tmp_path, monkeypatch, with_memory):
    from sage.config import ConfiguredVerificationCommand
    from sage.sandbox.base import CommandResult
    request, prepared, settings = _run_values(tmp_path)
    if with_memory:
        request = request.model_copy(update={"index_file": tmp_path / "graph.sqlite3"})
    settings = settings.model_copy(update={"verification_preflight": True,
        "verification_commands": (ConfiguredVerificationCommand(id="tests", command="python3 -m pytest"),)})
    monkeypatch.setattr("sage.workflows.solve.prepare_run", lambda *_: prepared)
    class MissingPython(FakeSandbox):
        def exec(self, command, **kwargs):
            return CommandResult(command, 127, "", "python unavailable")
    class Store(FakeStore):
        def write_verification_preflight(self, report):
            self.report = report
    class MustNotRun:
        def build_or_update_graph_tool(self, **kwargs):
            raise AssertionError("Retrieval must not build on failed preflight")
        async def solve(self, **kwargs):
            raise AssertionError("Models must not run on failed preflight")
    sandbox, store = MissingPython(), Store()
    with pytest.raises(WorkspaceError, match="before model calls"):
        asyncio.run(solve_issue(request, MustNotRun(), settings,
            sandbox_factory=lambda *_: sandbox, artifacts=store, retrieval_service=MustNotRun()))
    assert sandbox.stopped
    assert store.report["status"] == "unavailable"
    assert store.report["model_calls_started"] is False


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


def _retrieval_build(index_file: Path, indexed_sha: str) -> IndexBuildResult:
    return IndexBuildResult(
        build_type=IndexBuildType.FULL,
        index_file=index_file,
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


def _retrieval_retrieval(
    index_file: Path,
    indexed_sha: str,
    *,
    status: RetrievalStatus = RetrievalStatus.USED,
) -> RetrievalResult:
    used = status is RetrievalStatus.USED
    return RetrievalResult(
        status=status,
        outcome=(
            RetrievalOutcome.USEFUL_CONTEXT
            if used
            else RetrievalOutcome.NO_LEXICAL_CANDIDATES
        ),
        summary="Retrieved context." if used else "No retrieval matched.",
        index_file=index_file,
        repository_id="repository-id",
        indexed_sha=indexed_sha,
        search_modes=("fts",),
        total_candidates=1 if used else 0,
        returned=1 if used else 0,
        context="base graph context" if used else "",
        context_chars=18 if used else 0,
        items=(
            (
                RetrievalItem(
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
