from __future__ import annotations

import asyncio
import logging
import subprocess
from pathlib import Path

from sage.config import Settings
from sage.domain.admission import (
    DimensionAssessment,
    DimensionStatus,
    IntakeResult,
    ReadinessDimensions,
    ReadinessDisposition,
)
from sage.domain.planning import (
    AcceptanceCriterion,
    ExecutionPlan,
    PlanTask,
    VerificationHint,
)
from sage.domain.requests import PreparedRun
from sage.domain.results import SolveOutcome
from sage.domain.review import CriterionResult, ReviewResult, ReviewVerdict
from sage.domain.runtime import RuntimeContext
from sage.domain.usage import ModelRole
from sage.providers.base import ProviderResult
from sage.providers.factory import ProviderSet
from sage.repository import RepositoryTools
from sage.runtimes.v2.models import SolverResult, SolverStatus
from sage.runtimes.v2.runtime import V2GraphRuntime
from sage.sandbox.base import CommandResult


class LocalSandbox:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    def start(self) -> None:
        pass

    def exec(self, command: str, *, timeout_seconds: int | None = None) -> CommandResult:
        translated = command.replace("/workspace", str(self.workspace))
        result = subprocess.run(
            ["bash", "-lc", translated],
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

    def stop(self) -> None:
        pass


class ScriptedProvider:
    def __init__(self, provider: str, model: str, responses: list[object]) -> None:
        self.provider_name = provider
        self.model_name = model
        self.responses = responses
        self.calls: list[ModelRole] = []

    async def invoke_structured(self, *, role, messages, schema, timeout_seconds):
        del messages, schema, timeout_seconds
        self.calls.append(role)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return ProviderResult(
            parsed=response,
            provider=self.provider_name,
            model=self.model_name,
            input_tokens=100,
            output_tokens=50,
            cached_tokens=0,
            latency_ms=1,
        )


def test_v2_runtime_completes_three_provider_patch_path(
    tmp_path: Path,
    caplog,
) -> None:
    caplog.set_level(logging.INFO)
    workspace, base_sha = _repository(tmp_path)
    settings = Settings(
        runtime="v2-prototype",
        openai_api_key="openai-test",
        gemini_api_key="gemini-test",
        anthropic_api_key="anthropic-test",
        google_model_context_approved=True,
        runs_dir=tmp_path,
        command_timeout_seconds=10,
    )
    planner = ScriptedProvider("google", "gemini-3.7-flash", [_ready_intake()])
    solver = ScriptedProvider(
        "openai",
        "gpt-5.4-mini",
        [
            SolverResult(
                status=SolverStatus.IMPLEMENTED,
                summary="Updated the configured value.",
                patch=(
                    "diff --git a/app.py b/app.py\n"
                    "--- a/app.py\n"
                    "+++ b/app.py\n"
                    "@@ -1 +1 @@\n"
                    "-value = 1\n"
                    "+value = 2\n"
                ),
                changed_files_claimed=("app.py",),
            )
        ],
    )
    reviewer = ScriptedProvider(
        "anthropic",
        "claude-haiku-4-5",
        [
            ReviewResult(
                verdict=ReviewVerdict.PASS,
                criterion_results=(
                    CriterionResult(
                        criterion_id="value-updated",
                        satisfied=True,
                        evidence="The authoritative diff changes value to 2.",
                    ),
                ),
                confidence=0.99,
            )
        ],
    )
    unused_planner_fallback = ScriptedProvider("google", "fallback", [])
    unused_reviewer_fallback = ScriptedProvider("google", "fallback-review", [])
    runtime = V2GraphRuntime(
        settings,
        providers=ProviderSet(
            planner=planner,
            planner_fallback=unused_planner_fallback,
            solver=solver,
            reviewer=reviewer,
            reviewer_fallback=unused_reviewer_fallback,
        ),
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
        sandbox=sandbox,
        settings=settings,
    )

    result = asyncio.run(
        runtime.solve(
            issue_text="Change app.py value from 1 to 2.",
            context=RuntimeContext(
                prepared_run=prepared,
                sandbox=sandbox,
                repository=repository,
                settings=settings,
            ),
        )
    )

    assert result.outcome is SolveOutcome.COMPLETED
    assert [call.provider for call in result.provenance.calls] == [
        "google",
        "openai",
        "anthropic",
    ]
    assert (workspace / "app.py").read_text(encoding="utf-8") == "value = 2\n"
    assert repository.get_changed_files() == ["app.py"]
    assert (prepared.run_dir / "repository-map.json").is_file()
    assert (prepared.run_dir / "autonomy-contract.json").is_file()
    assert (prepared.run_dir / "verification-summary.json").is_file()
    assert (prepared.run_dir / "review.json").is_file()
    assert (prepared.run_dir / "terminal.json").is_file()
    assert unused_planner_fallback.calls == []
    assert unused_reviewer_fallback.calls == []
    assert "Planner: started stage=intake-planner" in caplog.text
    assert "Solver: started stage=solver" in caplog.text
    assert "Verifier: finished pass=1 status=pass" in caplog.text
    assert "Reviewer: started stage=review" in caplog.text
    assert "V2 workflow: finished run=v2-test outcome=completed model_calls=3" in caplog.text


def test_v2_runtime_stops_after_planner_for_clarification(tmp_path: Path) -> None:
    workspace, base_sha = _repository(tmp_path)
    settings = Settings(
        runtime="v2-prototype",
        openai_api_key="openai-test",
        gemini_api_key="gemini-test",
        anthropic_api_key="anthropic-test",
        google_model_context_approved=True,
        runs_dir=tmp_path,
        command_timeout_seconds=10,
    )
    from sage.domain.admission import BlockingQuestion

    intake = IntakeResult(
        disposition=ReadinessDisposition.NEEDS_HUMAN_INFORMATION,
        dimensions=_dimensions(DimensionStatus.INSUFFICIENT),
        rationale="Expected behavior is missing.",
        blocking_questions=(
            BlockingQuestion(
                question="What should the new value be?",
                why_blocking="The repository does not define it.",
            ),
        ),
    )
    planner = ScriptedProvider("google", "gemini-3.7-flash", [intake])
    solver = ScriptedProvider("openai", "gpt-5.4-mini", [])
    reviewer = ScriptedProvider("anthropic", "claude-haiku-4-5", [])
    runtime = V2GraphRuntime(
        settings,
        providers=ProviderSet(
            planner=planner,
            planner_fallback=ScriptedProvider("google", "fallback", []),
            solver=solver,
            reviewer=reviewer,
            reviewer_fallback=ScriptedProvider("google", "fallback-review", []),
        ),
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    prepared = PreparedRun(
        run_id="v2-test",
        source_repo=workspace,
        run_dir=run_dir,
        workspace_dir=workspace,
        base_ref="HEAD",
        base_sha=base_sha,
    )
    sandbox = LocalSandbox(workspace)

    result = asyncio.run(
        runtime.solve(
            issue_text="Change the value.",
            context=RuntimeContext(
                prepared_run=prepared,
                sandbox=sandbox,
                repository=RepositoryTools(
                    workspace_root=workspace,
                    sandbox=sandbox,
                    settings=settings,
                ),
                settings=settings,
            ),
        )
    )

    assert result.outcome is SolveOutcome.NEEDS_HUMAN_INFORMATION
    assert result.clarification is not None
    assert len(result.provenance.calls) == 1
    assert solver.calls == []
    assert reviewer.calls == []


def test_v2_runtime_repairs_one_hard_verification_failure(tmp_path: Path) -> None:
    workspace, base_sha = _repository(tmp_path)
    settings = Settings(
        runtime="v2-prototype",
        openai_api_key="openai-test",
        gemini_api_key="gemini-test",
        anthropic_api_key="anthropic-test",
        google_model_context_approved=True,
        runs_dir=tmp_path,
        command_timeout_seconds=10,
    )
    planner = ScriptedProvider(
        "google",
        "gemini-3.7-flash",
        [_ready_intake(with_value_check=True)],
    )
    solver = ScriptedProvider(
        "openai",
        "gpt-5.4-mini",
        [
            SolverResult(
                status=SolverStatus.IMPLEMENTED,
                summary="Initial candidate.",
                patch=(
                    "diff --git a/app.py b/app.py\n"
                    "--- a/app.py\n+++ b/app.py\n"
                    "@@ -1 +1 @@\n-value = 1\n+value = 3\n"
                ),
            ),
            SolverResult(
                status=SolverStatus.IMPLEMENTED,
                summary="Corrected candidate.",
                patch=(
                    "diff --git a/app.py b/app.py\n"
                    "--- a/app.py\n+++ b/app.py\n"
                    "@@ -1 +1 @@\n-value = 3\n+value = 2\n"
                ),
            ),
        ],
    )
    reviewer = ScriptedProvider(
        "anthropic",
        "claude-haiku-4-5",
        [
            ReviewResult(
                verdict=ReviewVerdict.PASS,
                criterion_results=(
                    CriterionResult(
                        criterion_id="value-updated",
                        satisfied=True,
                        evidence="The corrected value is 2.",
                    ),
                ),
                confidence=0.99,
            )
        ],
    )
    runtime = V2GraphRuntime(
        settings,
        providers=ProviderSet(
            planner=planner,
            planner_fallback=ScriptedProvider("google", "fallback", []),
            solver=solver,
            reviewer=reviewer,
            reviewer_fallback=ScriptedProvider("google", "fallback-review", []),
        ),
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    sandbox = LocalSandbox(workspace)

    result = asyncio.run(
        runtime.solve(
            issue_text="Change app.py value from 1 to 2.",
            context=RuntimeContext(
                prepared_run=PreparedRun(
                    run_id="v2-repair",
                    source_repo=workspace,
                    run_dir=run_dir,
                    workspace_dir=workspace,
                    base_ref="HEAD",
                    base_sha=base_sha,
                ),
                sandbox=sandbox,
                repository=RepositoryTools(
                    workspace_root=workspace,
                    sandbox=sandbox,
                    settings=settings,
                ),
                settings=settings,
            ),
        )
    )

    assert result.outcome is SolveOutcome.COMPLETED
    assert result.provenance.implementation_repairs == 1
    assert [record.role for record in result.provenance.calls] == [
        ModelRole.PLANNER,
        ModelRole.SOLVER,
        ModelRole.SOLVER,
        ModelRole.REVIEWER,
    ]
    assert (workspace / "app.py").read_text() == "value = 2\n"


def _ready_intake(*, with_value_check: bool = False) -> IntakeResult:
    return IntakeResult(
        disposition=ReadinessDisposition.READY_AUTONOMOUS,
        dimensions=_dimensions(DimensionStatus.SUFFICIENT),
        rationale="The change is explicit and locally testable.",
        plan=ExecutionPlan(
            task_summary="Update the configured value.",
            acceptance_contract=(
                AcceptanceCriterion(
                    criterion_id="value-updated",
                    behavior="app.py sets value to 2.",
                    verification="Inspect the diff and run git diff --check.",
                ),
            ),
            tasks=(
                PlanTask(
                    task_id="update-value",
                    objective="Change the value assignment.",
                    relevant_paths=("app.py",),
                    criterion_ids=("value-updated",),
                ),
            ),
            allowed_write_scopes=("app.py",),
            verification_hints=(
                (
                    VerificationHint(
                        command=(
                            'python3 -c "assert open(\'app.py\').read() '
                            '== \'value = 2\\n\'"'
                        ),
                        reason="Verify the requested value.",
                    ),
                )
                if with_value_check
                else ()
            ),
        ),
    )


def _dimensions(default: DimensionStatus) -> ReadinessDimensions:
    sufficient = DimensionAssessment(status=default, evidence="Fixture evidence.")
    return ReadinessDimensions(
        objective_clarity=sufficient,
        expected_behavior_clarity=sufficient,
        acceptance_testability=sufficient,
        scope_boundedness=sufficient,
        repository_evidence_sufficiency=sufficient,
        design_choice_closed=sufficient,
        external_dependency_availability=sufficient,
        sandbox_compatibility=sufficient,
        permission_or_credential_independence=sufficient,
        cross_repository_dependency=sufficient,
        human_approval_dependency=sufficient,
    )


def _repository(tmp_path: Path) -> tuple[Path, str]:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=workspace, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=workspace,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=workspace,
        check=True,
    )
    (workspace / "app.py").write_text("value = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=workspace, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=workspace, check=True, capture_output=True)
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return workspace, base_sha
