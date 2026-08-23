import asyncio
from pathlib import Path

import pytest

from sage.config import Settings
from sage.domain.requests import PreparedRun, SolveRequest
from sage.domain.results import AgentFinalOutput, SolveOutcome
from sage.errors import AgentRuntimeError, WorkspaceError
from sage.workflow.solve import solve_issue


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


class FakeStore:
    def __init__(self) -> None:
        self.initialized = False
        self.persisted = False

    def initialize_run(self, **kwargs) -> None:
        self.initialized = True

    def persist_result(self, **kwargs) -> None:
        self.persisted = True


class SuccessfulRuntime:
    async def solve(self, *, issue_text: str, context) -> AgentFinalOutput:
        assert issue_text == "Fix it."
        return AgentFinalOutput(summary="Fixed.")


class FailingRuntime:
    async def solve(self, *, issue_text: str, context) -> AgentFinalOutput:
        raise AgentRuntimeError("model failed")


class InconsistentV2Runtime:
    async def solve(self, *, issue_text: str, context) -> AgentFinalOutput:
        return AgentFinalOutput(
            summary="Human approval is required.",
            outcome=SolveOutcome.HUMAN_REQUIRED,
        )


def test_solve_issue_uses_git_results_and_cleans_up(tmp_path: Path, monkeypatch) -> None:
    request, prepared, settings = _run_values(tmp_path)
    monkeypatch.setattr("sage.workflow.solve.prepare_run", lambda *_: prepared)
    sandbox = FakeSandbox()
    repository = FakeRepository()
    store = FakeStore()

    result = asyncio.run(
        solve_issue(
            request,
            SuccessfulRuntime(),
            settings,
            sandbox_factory=lambda *_: sandbox,
            repository_factory=lambda *_: repository,
            artifact_store=store,
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
    monkeypatch.setattr("sage.workflow.solve.prepare_run", lambda *_: prepared)
    sandbox = FakeSandbox()

    with pytest.raises(AgentRuntimeError, match="model failed"):
        asyncio.run(
            solve_issue(
                request,
                FailingRuntime(),
                settings,
                sandbox_factory=lambda *_: sandbox,
                repository_factory=lambda *_: FakeRepository(),
                artifact_store=FakeStore(),
            )
        )

    assert sandbox.started is True
    assert sandbox.stopped is True


def test_solve_issue_rejects_pre_mutation_terminal_with_diff(
    tmp_path: Path,
    monkeypatch,
) -> None:
    request, prepared, settings = _run_values(tmp_path)
    settings = settings.model_copy(
        update={
            "runtime": "v2-prototype",
            "gemini_api_key": "gemini-test",
            "anthropic_api_key": "anthropic-test",
            "google_model_context_approved": True,
        }
    )
    monkeypatch.setattr("sage.workflow.solve.prepare_run", lambda *_: prepared)
    sandbox = FakeSandbox()

    with pytest.raises(WorkspaceError, match="pre-mutation"):
        asyncio.run(
            solve_issue(
                request,
                InconsistentV2Runtime(),
                settings,
                sandbox_factory=lambda *_: sandbox,
                repository_factory=lambda *_: FakeRepository(),
                artifact_store=FakeStore(),
            )
        )

    assert sandbox.stopped is True


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
