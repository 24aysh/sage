from __future__ import annotations

import asyncio
import logging
import re
import subprocess
from pathlib import Path
from typing import Any, Callable

from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable

from sage.config import Settings
from sage.domain.admission import (
    AdmissionResult,
    BlockingQuestion,
    ClarificationPacket,
    ReadinessDisposition,
)
from sage.domain.requests import PreparedRun
from sage.domain.results import SolveOutcome
from sage.domain.review import (
    CriterionResult,
    ReviewFailureType,
    ReviewFinding,
    ReviewResult,
    ReviewVerdict,
)
from sage.domain.runtime import RuntimeContext
from sage.domain.solver import SolverFinalResult, SolverOutcome
from sage.providers.base import ProviderResult
from sage.providers.factory import ProviderSet
from sage.repository import RepositoryTools
from sage.runtimes.v2.runtime import V2GraphRuntime
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


class AdmissionModel(Runnable[Any, AIMessage]):
    def __init__(self) -> None:
        self.turn = 0

    def invoke(self, input: Any, config=None, **kwargs: Any) -> AIMessage:
        del input, config, kwargs
        raise AssertionError("Admission must use asynchronous model calls.")

    async def ainvoke(self, input: Any, config=None, **kwargs: Any) -> AIMessage:
        del config, kwargs
        self.turn += 1
        if self.turn == 1:
            return _tool(
                "save_admission_context",
                {
                    "summary": "The desired value is not defined.",
                    "requirements": [
                        {
                            "requirement_id": "value",
                            "statement": "Choose the new value.",
                            "evidence_ids": ["current"],
                            "status": "blocked",
                        }
                    ],
                    "relevant_paths": ["app.py"],
                    "relevant_symbols": ["value"],
                    "repository_conventions": [],
                    "candidate_verification_commands": [],
                    "assumptions": [],
                    "open_questions": ["What value should be used?"],
                    "repository_evidence": [
                        {
                            "evidence_id": "current",
                            "path": "app.py",
                            "line_start": 1,
                            "line_end": 1,
                            "title": "Current value",
                        }
                    ],
                    "research_evidence": [],
                },
                "save-context",
            )
        messages = list(input)
        digest_match = re.search(
            r"\(([0-9a-f]{64})\)",
            str(messages[-1].content),
        )
        assert digest_match is not None
        result = AdmissionResult(
            disposition=ReadinessDisposition.NEEDS_HUMAN_INFORMATION,
            summary="The expected value is missing.",
            rationale="The repository defines only the current value.",
            confidence=0.99,
            context_digest=digest_match.group(1),
            clarification=ClarificationPacket(
                round=1,
                disposition=ReadinessDisposition.NEEDS_HUMAN_INFORMATION,
                summary="Sage needs the expected value before editing.",
                questions=(
                    BlockingQuestion(
                        question="What should the new value be?",
                        why_blocking="Different values produce different behavior.",
                        repository_evidence=("app.py:1 defines only the current value.",),
                    ),
                ),
                rerun_instruction="Reply and rerun.",
            ),
        )
        return AIMessage(
            content="",
            additional_kwargs={"parsed": result.model_dump(mode="json")},
        )


class AdmissionBindingModel:
    def __init__(self) -> None:
        self.tool_names: list[str] = []

    def bind_tools(self, tools, **kwargs):
        assert kwargs["response_format"] is AdmissionResult
        self.tool_names = [tool.name for tool in tools]
        return AdmissionModel()


class ReadyBindingModel:
    def __init__(self) -> None:
        self.bound_tool_names: list[list[str]] = []

    def bind_tools(self, tools, **kwargs):
        names = [tool.name for tool in tools]
        self.bound_tool_names.append(names)
        schema = kwargs["response_format"]
        if schema is AdmissionResult:
            return ScriptedModel(
                [
                    _tool(
                        "save_admission_context",
                        {
                            "summary": "The requested file and value are defined.",
                            "requirements": [
                                {
                                    "requirement_id": "value",
                                    "statement": "Set the value to two.",
                                    "evidence_ids": ["current"],
                                    "status": "supported",
                                }
                            ],
                            "relevant_paths": ["app.py"],
                            "relevant_symbols": ["value"],
                            "repository_conventions": [],
                            "candidate_verification_commands": [],
                            "assumptions": [],
                            "open_questions": [],
                            "repository_evidence": [
                                {
                                    "evidence_id": "current",
                                    "path": "app.py",
                                    "line_start": 1,
                                    "line_end": 1,
                                    "title": "Current value",
                                }
                            ],
                            "research_evidence": [],
                        },
                        "save-ready-context",
                    ),
                    _ready_admission_final,
                ]
            )
        assert schema is SolverFinalResult
        return ScriptedModel(
            [
                _ready_plan_tool,
                _tool(
                    "replace_text",
                    {
                        "path": "app.py",
                        "old_text": "value = 1",
                        "new_text": "value = 2",
                        "expected_occurrences": 1,
                    },
                    "ready-edit",
                ),
                _final("implemented from Admission context"),
            ]
        )


def test_v2_tool_solver_can_complete_two_review_repairs(
    tmp_path: Path,
    caplog,
) -> None:
    caplog.set_level(logging.INFO)
    workspace, base_sha = _repository(tmp_path)
    settings = Settings(
        runtime="v2-prototype",
        openai_api_key="openai-test",
        gemini_api_key="gemini-test",
        v2_solver_model="solver-model",
        v2_reviewer_model="reviewer-model",
        v2_admission_enabled=False,
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
        run_id="v2-test",
        source_repo=workspace,
        run_dir=tmp_path / "run",
        workspace_dir=workspace,
        base_ref="HEAD",
        base_sha=base_sha,
    )
    prepared.run_dir.mkdir()
    sandbox = LocalSandbox(workspace)
    repository = RepositoryTools(
        workspace_root=workspace,
        sandbox=sandbox,  # type: ignore[arg-type]
        settings=settings,
    )
    runtime = V2GraphRuntime(
        settings,
        providers=ProviderSet(reviewer=reviewer),
        solver_model=solver,  # type: ignore[arg-type]
    )

    result = asyncio.run(
        runtime.solve(
            issue_text="Change app.py to the reviewer-approved value.",
            context=RuntimeContext(
                prepared_run=prepared,
                sandbox=sandbox,  # type: ignore[arg-type]
                repository=repository,
                settings=settings,
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
    assert (prepared.run_dir / "solver-plan.json").is_file()
    assert (prepared.run_dir / "solver-final.json").is_file()
    assert (prepared.run_dir / "reviews/03.json").is_file()
    assert "Solver: activity" in caplog.text
    assert "Reviewer: activity" in caplog.text
    assert "Planner:" not in caplog.text


def test_v2_admission_halts_with_questions_before_solver_or_publication(
    tmp_path: Path,
    caplog,
) -> None:
    caplog.set_level(logging.INFO)
    workspace, base_sha = _repository(tmp_path)
    settings = Settings(
        runtime="v2-prototype",
        openai_api_key="openai-test",
        gemini_api_key="gemini-test",
        v2_solver_model="solver-model",
        v2_reviewer_model="reviewer-model",
        command_timeout_seconds=10,
        run_deadline_seconds=600,
        finalization_reserve_seconds=60,
    )
    prepared = PreparedRun(
        run_id="admission-test",
        source_repo=workspace,
        run_dir=tmp_path / "run-admission",
        workspace_dir=workspace,
        base_ref="HEAD",
        base_sha=base_sha,
    )
    prepared.run_dir.mkdir()
    sandbox = LocalSandbox(workspace)
    repository = RepositoryTools(
        workspace_root=workspace,
        sandbox=sandbox,  # type: ignore[arg-type]
        settings=settings,
    )
    admission_model = AdmissionBindingModel()
    runtime = V2GraphRuntime(
        settings,
        providers=ProviderSet(reviewer=ReviewerProvider([])),
        solver_model=admission_model,  # type: ignore[arg-type]
    )

    result = asyncio.run(
        runtime.solve(
            issue_text="Change app.py, but the expected value is not specified.",
            context=RuntimeContext(
                prepared_run=prepared,
                sandbox=sandbox,  # type: ignore[arg-type]
                repository=repository,
                settings=settings,
            ),
        )
    )

    assert result.outcome is SolveOutcome.NEEDS_HUMAN_INFORMATION
    assert result.clarification is not None
    assert result.clarification.questions[0].repository_evidence
    assert (workspace / "app.py").read_text(encoding="utf-8") == "value = 1\n"
    assert (prepared.run_dir / "admission-context.json").is_file()
    assert (prepared.run_dir / "admission-final.json").is_file()
    assert (prepared.run_dir / "clarification.json").is_file()
    assert "save_plan" not in admission_model.tool_names
    assert "write_file" not in admission_model.tool_names
    assert "Admission: activity" in caplog.text
    assert "Solver: activity" not in caplog.text


def test_v2_ready_admission_context_is_reused_by_solver_and_reviewer(
    tmp_path: Path,
) -> None:
    workspace, base_sha = _repository(tmp_path)
    settings = Settings(
        runtime="v2-prototype",
        openai_api_key="openai-test",
        gemini_api_key="gemini-test",
        v2_solver_model="solver-model",
        v2_reviewer_model="reviewer-model",
        command_timeout_seconds=10,
        run_deadline_seconds=600,
        finalization_reserve_seconds=60,
    )
    prepared = PreparedRun(
        run_id="ready-admission-test",
        source_repo=workspace,
        run_dir=tmp_path / "run-ready",
        workspace_dir=workspace,
        base_ref="HEAD",
        base_sha=base_sha,
    )
    prepared.run_dir.mkdir()
    sandbox = LocalSandbox(workspace)
    repository = RepositoryTools(
        workspace_root=workspace,
        sandbox=sandbox,  # type: ignore[arg-type]
        settings=settings,
    )
    model = ReadyBindingModel()
    reviewer = ReviewerProvider(
        [
            ReviewResult(
                verdict=ReviewVerdict.PASS,
                criterion_results=(
                    CriterionResult(
                        criterion_id="value",
                        satisfied=True,
                        evidence="The actual diff sets the value to two.",
                    ),
                ),
                confidence=0.99,
            )
        ]
    )
    runtime = V2GraphRuntime(
        settings,
        providers=ProviderSet(reviewer=reviewer),
        solver_model=model,  # type: ignore[arg-type]
    )

    result = asyncio.run(
        runtime.solve(
            issue_text="Set app.py value to two.",
            context=RuntimeContext(
                prepared_run=prepared,
                sandbox=sandbox,  # type: ignore[arg-type]
                repository=repository,
                settings=settings,
            ),
        )
    )

    assert result.outcome is SolveOutcome.COMPLETED
    assert result.provenance is not None
    assert result.provenance.admission_sessions == 1
    assert result.provenance.solver_sessions == 1
    assert (workspace / "app.py").read_text(encoding="utf-8") == "value = 2\n"
    assert "base-admission-context" in reviewer.messages[0]
    assert "current" in reviewer.messages[0]
    assert "read_file" in model.bound_tool_names[0]
    assert "replace_text" not in model.bound_tool_names[0]
    assert "replace_text" in model.bound_tool_names[1]


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


def _context_digest(input: Any) -> str:
    text = "\n".join(str(message.content) for message in input)
    matches = re.findall(r'"digest": "([0-9a-f]{64})"|\(([0-9a-f]{64})\)', text)
    assert matches
    first, second = matches[-1]
    return first or second


def _ready_admission_final(input: Any) -> AIMessage:
    result = AdmissionResult(
        disposition=ReadinessDisposition.READY,
        summary="The task is ready.",
        rationale="Repository evidence defines the requested file and behavior.",
        confidence=0.99,
        context_digest=_context_digest(input),
    )
    return AIMessage(
        content="",
        additional_kwargs={"parsed": result.model_dump(mode="json")},
    )


def _ready_plan_tool(input: Any) -> AIMessage:
    args = _plan_args()
    args.update(
        {
            "admission_context_digest": _context_digest(input),
            "admission_evidence_ids": ["current"],
        }
    )
    return _tool("save_plan", args, "ready-plan")


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
