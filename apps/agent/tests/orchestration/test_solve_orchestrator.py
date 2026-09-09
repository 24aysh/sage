from __future__ import annotations

import asyncio
import logging
import subprocess
from pathlib import Path
from typing import Any, Callable

from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable

from sage.agents.reviewer import ReviewerAgent
from sage.agents.solver import SolverAgent, SolverPlanSession
from sage.artifacts.store import RunArtifacts
from sage.config import Settings
from sage.domain.review import (
    CriterionResult,
    ReviewFailureType,
    ReviewFinding,
    ReviewResult,
    ReviewVerdict,
)
from sage.domain.memory import (
    MemoryRetrievalOutcome,
    MemoryRetrievalResult,
    MemoryRetrievalStatus,
)
from sage.domain.solve import PreparedRun, SolveOutcome
from sage.domain.solver import SolverFinalResult, SolverOutcome
from sage.orchestration.context import SolveContext
from sage.orchestration.solve import SolveOrchestrator
from sage.providers.base import ProviderResult
from sage.providers.calls import ModelCalls
from sage.legion_memory.service import LegionMemoryService
from sage.legion_memory.session import MemorySession
from sage.research.service import build_research_service
from sage.repository.service import Repository
from sage.sandbox.base import CommandResult


class LocalSandbox:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    def exec(self, command: str, *, timeout_seconds: int | None = None) -> CommandResult:
        result = subprocess.run(
            ["bash", "-lc", command.replace("/workspace", str(self.workspace))],
            cwd=self.workspace,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        return CommandResult(
            command=command,
            exit_code=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )


class ScriptedModel(Runnable[Any, AIMessage]):
    def __init__(
        self,
        responses: list[AIMessage | Callable[[Any], AIMessage]],
    ) -> None:
        self.responses = responses

    def invoke(self, input: Any, config=None, **kwargs: Any) -> AIMessage:
        del input, config, kwargs
        raise AssertionError("Solver must use asynchronous model calls.")

    async def ainvoke(self, input: Any, config=None, **kwargs: Any) -> AIMessage:
        del config, kwargs
        response = self.responses.pop(0)
        return response(input) if callable(response) else response


class BindingModel:
    def __init__(self, sessions: list[list[AIMessage]]) -> None:
        self.sessions = sessions
        self.bound_tool_names: list[list[str]] = []

    def bind_tools(self, tools, **kwargs):
        assert kwargs["response_format"] is SolverFinalResult
        assert kwargs["parallel_tool_calls"] is False
        names = [tool.name for tool in tools]
        self.bound_tool_names.append(names)
        assert "apply_patch" not in names
        return ScriptedModel(self.sessions.pop(0))


class ReviewerProvider:
    provider_name = "google"
    model_name = "reviewer-model"

    def __init__(self, reviews: list[ReviewResult]) -> None:
        self.reviews = reviews
        self.messages: list[str] = []

    async def invoke_structured(self, **kwargs):
        self.messages.append(str(kwargs["messages"][-1].content))
        review = self.reviews.pop(0)
        return ProviderResult(
            parsed=review,
            provider=self.provider_name,
            model=self.model_name,
            input_tokens=10,
            output_tokens=5,
            cached_tokens=0,
            latency_ms=1,
        )


def test_solver_and_reviewer_complete_two_feedback_repairs(
    tmp_path: Path,
    caplog,
) -> None:
    caplog.set_level(logging.INFO)
    workspace, base_sha = _repository(tmp_path)
    settings = Settings(
        openai_api_key="openai-test",
        gemini_api_key="gemini-test",
        solver_model="solver-model",
        reviewer_model="reviewer-model",
        command_timeout_seconds=10,
        run_deadline_seconds=600,
        finalization_reserve_seconds=60,
    )
    solver = BindingModel(
        [
            [
                _tool("save_plan", _plan_args(), "plan"),
                _tool(
                    "replace_text",
                    {
                        "path": "app.py",
                        "old_text": "value = 1",
                        "new_text": "value = 2",
                        "expected_occurrences": 1,
                    },
                    "edit-1",
                ),
                _final("initial implementation"),
            ],
            [
                _tool(
                    "replace_text",
                    {
                        "path": "app.py",
                        "old_text": "value = 2",
                        "new_text": "value = 3",
                        "expected_occurrences": 1,
                    },
                    "edit-2",
                ),
                _final("first repair"),
            ],
            [
                _tool(
                    "replace_text",
                    {
                        "path": "app.py",
                        "old_text": "value = 3",
                        "new_text": "value = 4",
                        "expected_occurrences": 1,
                    },
                    "edit-3",
                ),
                _final("second repair"),
            ],
        ]
    )
    reviewer = ReviewerProvider(
        [
            _failed_review("Set value to three."),
            _failed_review("Set value to four."),
            ReviewResult(
                verdict=ReviewVerdict.PASS,
                criterion_results=(
                    CriterionResult(
                        criterion_id="value",
                        satisfied=True,
                        evidence="The actual Git diff sets value to four.",
                    ),
                ),
                confidence=0.99,
            ),
        ]
    )
    prepared = PreparedRun(
        run_id="solve-test",
        source_repo=workspace,
        run_dir=tmp_path / "run",
        workspace_dir=workspace,
        base_ref="HEAD",
        base_sha=base_sha,
    )
    prepared.run_dir.mkdir()
    sandbox = LocalSandbox(workspace)
    repository = Repository(
        workspace_root=workspace,
        sandbox=sandbox,  # type: ignore[arg-type]
        settings=settings,
    )
    orchestrator = SolveOrchestrator(
        solver=SolverAgent(settings=settings, model=solver),  # type: ignore[arg-type]
        reviewer=ReviewerAgent(settings=settings),
        reviewer_provider=reviewer,
        research_service=build_research_service(settings),
    )

    result = asyncio.run(
        orchestrator.solve(
            issue_text="Change app.py to the reviewer-approved value.",
            context=SolveContext(
                prepared_run=prepared,
                repository=repository,
                settings=settings,
                artifacts=RunArtifacts(prepared.run_dir),
            ),
        )
    )

    assert result.outcome is SolveOutcome.COMPLETED
    assert (workspace / "app.py").read_text(encoding="utf-8") == "value = 4\n"
    assert result.provenance is not None
    assert result.provenance.solver_sessions == 3
    assert result.provenance.review_cycles == 3
    assert len(result.provenance.calls) > 6
    assert len(reviewer.messages) == 3
    assert "Change app.py" in reviewer.messages[0]
    assert "saved-solver-plan" in reviewer.messages[0]
    assert "actual-git-diff" in reviewer.messages[0]
    assert all("admission" not in message.lower() for message in reviewer.messages)
    assert (prepared.run_dir / "solver-plan.json").is_file()
    assert (prepared.run_dir / "solver-final.json").is_file()
    assert (prepared.run_dir / "reviews/03.json").is_file()
    assert not any(path.name.startswith("admission") for path in prepared.run_dir.iterdir())
    assert "Solver: activity" in caplog.text
    assert "Reviewer: activity" in caplog.text
    assert "Admission:" not in caplog.text


def test_solver_uses_memory_locator_then_verifies_current_source(tmp_path: Path) -> None:
    workspace, base_sha = _repository(tmp_path)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    settings = Settings(
        openai_api_key="openai-test",
        gemini_api_key="gemini-test",
        solver_model="solver-model",
        run_deadline_seconds=600,
        finalization_reserve_seconds=60,
    )
    service = LegionMemoryService(data_root=tmp_path / "memory")
    build = service.build_or_update_graph_tool(repo_root=workspace)
    memory = MemorySession(
        service=service,
        repo_root=workspace,
        requested_memory_file=build.memory_file,
        memory_file=build.memory_file,
        build=build,
        retrieval=MemoryRetrievalResult(
            status=MemoryRetrievalStatus.USED,
            outcome=MemoryRetrievalOutcome.USEFUL_CONTEXT,
            summary="Found app.py.",
            memory_file=build.memory_file,
            repository_id=build.repository_id,
            indexed_sha=base_sha,
            search_modes=("exact",),
            total_candidates=1,
            returned=1,
            context="File app.py at app.py:1-1",
            context_chars=26,
            duration_ms=1,
        ),
    )
    model = BindingModel(
        [
            [
                _tool(
                    "semantic_search_nodes_tool",
                    {"query": "app.py", "limit": 5},
                    "memory",
                ),
                _tool("read_file", {"path": "app.py"}, "source"),
                _tool("save_plan", _plan_args(), "plan"),
                _final("inspected memory and current source"),
            ]
        ]
    )
    prepared = PreparedRun(
        run_id="memory-solver-test",
        source_repo=workspace,
        run_dir=run_dir,
        workspace_dir=workspace,
        base_ref="HEAD",
        base_sha=base_sha,
    )
    artifacts = RunArtifacts(run_dir)
    repository = Repository(
        workspace_root=workspace,
        sandbox=LocalSandbox(workspace),  # type: ignore[arg-type]
        settings=settings,
    )
    context = SolveContext(
        prepared_run=prepared,
        repository=repository,
        settings=settings,
        artifacts=artifacts,
        memory=memory,
    )
    calls = ModelCalls(
        settings=settings,
        reviewer=ReviewerProvider([]),  # type: ignore[arg-type]
    )

    result = asyncio.run(
        SolverAgent(settings=settings, model=model).run(  # type: ignore[arg-type]
            stage="solver",
            message=(
                "<untrusted-legion-memory>\nFile app.py at app.py:1-1\n"
                "</untrusted-legion-memory>"
            ),
            context=context,
            plans=SolverPlanSession(artifacts),
            calls=calls,
            research=build_research_service(settings),
        )
    )

    assert result.outcome.value == "implemented"
    assert "semantic_search_nodes_tool" in model.bound_tool_names[0]
    assert [record.tool_name for record in calls.provenance().tool_calls] == [
        "semantic_search_nodes_tool",
        "read_file",
        "save_plan",
    ]
    assert memory.tool_calls[0].tool_name == "semantic_search_nodes_tool"


def _repository(tmp_path: Path) -> tuple[Path, str]:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q", workspace], check=True)
    subprocess.run(["git", "-C", workspace, "config", "user.name", "Test"], check=True)
    subprocess.run(
        ["git", "-C", workspace, "config", "user.email", "test@example.invalid"],
        check=True,
    )
    (workspace / "app.py").write_text("value = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", workspace, "add", "app.py"], check=True)
    subprocess.run(["git", "-C", workspace, "commit", "-qm", "base"], check=True)
    base_sha = subprocess.check_output(
        ["git", "-C", workspace, "rev-parse", "HEAD"],
        text=True,
    ).strip()
    return workspace, base_sha


def _tool(name: str, args: dict[str, object], call_id: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}],
    )


def _final(summary: str) -> AIMessage:
    result = SolverFinalResult(
        outcome=SolverOutcome.IMPLEMENTED,
        summary=summary,
        plan_version=1,
        verification_claims=("Inspected the candidate.",),
    )
    return AIMessage(content="", additional_kwargs={"parsed": result.model_dump()})


def _plan_args() -> dict[str, object]:
    return {
        "issue_summary": "Change app.py to the accepted value.",
        "approach": "Update the value and inspect the Git candidate.",
        "tasks": [
            {
                "task_id": "edit",
                "objective": "Update app.py.",
                "expected_paths": ["app.py"],
                "criterion_ids": ["value"],
            }
        ],
        "acceptance_criteria": [
            {"criterion_id": "value", "requirement": "Reviewer accepts the value."}
        ],
        "relevant_paths": ["app.py"],
        "verification_commands": [],
        "assumptions": [],
        "risks": [],
        "status": "implementable",
        "blocker": None,
    }


def _failed_review(required: str) -> ReviewResult:
    return ReviewResult(
        verdict=ReviewVerdict.FAIL,
        failure_type=ReviewFailureType.IMPLEMENTATION,
        blocking_findings=(
            ReviewFinding(
                finding_id="value",
                criterion_ids=("value",),
                evidence="The actual candidate value is not yet accepted.",
                required_outcome=required,
                path="app.py",
                line=1,
            ),
        ),
        criterion_results=(
            CriterionResult(
                criterion_id="value",
                satisfied=False,
                evidence="The candidate needs another repair.",
            ),
        ),
        confidence=0.95,
    )
