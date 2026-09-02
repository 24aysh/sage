from __future__ import annotations

from pathlib import Path

from sage.artifacts.store import RunArtifacts
from sage.config import ConfiguredVerificationCommand, Settings
from sage.domain.solver import SolverAcceptanceCriterion, SolverPlan, SolverPlanTask
from sage.domain.verification import VerificationSource, VerificationStatus
from sage.sandbox.base import CommandResult
from sage.verification.discovery import discover_solver_verification_commands
from sage.verification.runner import Verifier


class FakeRepository:
    def __init__(self, results: list[CommandResult] | None = None) -> None:
        self.calls: list[str] = []
        self.results = results or []

    def run_command(
        self,
        *,
        command: str,
        timeout_seconds: int | None = None,
    ) -> CommandResult:
        del timeout_seconds
        self.calls.append(command)
        if self.results:
            return self.results.pop(0)
        return CommandResult(
            command=command,
            exit_code=0,
            stdout="authorization: top-secret\n",
            stderr="",
        )

    def get_complete_diff(self) -> str:
        return "diff --git a/app.py b/app.py\n"


def _plan() -> SolverPlan:
    return SolverPlan(
        issue_summary="Update app.",
        approach="Edit and verify app.py.",
        acceptance_criteria=(
            SolverAcceptanceCriterion(
                criterion_id="updated",
                requirement="App is updated.",
            ),
        ),
        tasks=(
            SolverPlanTask(
                task_id="update",
                objective="Update app.",
                criterion_ids=("updated",),
            ),
        ),
        status="implementable",
    )


def test_discovery_orders_mandatory_then_trusted_configured_commands() -> None:
    commands = discover_solver_verification_commands(
        plan=_plan(),
        settings=Settings(
            openai_api_key="test",
            command_timeout_seconds=30,
            verification_commands=(
                ConfiguredVerificationCommand(
                    id="focused",
                    command="pytest -q tests/test_app.py",
                    timeout_seconds=90,
                ),
            ),
        ),
    )

    assert [item.check_id for item in commands] == ["git-diff-check", "focused"]
    assert commands[1].source is VerificationSource.CONFIGURED
    assert commands[1].timeout_seconds == 30


def test_verifier_runs_sequentially_and_redacts_secret_like_logs(tmp_path: Path) -> None:
    repository = FakeRepository()
    artifacts = RunArtifacts(tmp_path)
    commands = discover_solver_verification_commands(
        plan=_plan(),
        settings=Settings(openai_api_key="test", command_timeout_seconds=30),
    )

    result = Verifier(
        repository=repository,  # type: ignore[arg-type]
        artifacts=artifacts,
        max_log_chars=4_000,
    ).verify(commands, pass_number=1)

    assert result.status is VerificationStatus.PASS
    assert repository.calls == ["git diff --check HEAD --"]
    log = (tmp_path / "verification/pass-1/git-diff-check.log").read_text()
    assert "top-secret" not in log
    assert "authorization=[redacted]" in log
    assert (tmp_path / "verification-summary.json").is_file()


def test_optional_failure_is_visible_without_blocking_required_success(
    tmp_path: Path,
) -> None:
    commands = discover_solver_verification_commands(
        plan=_plan(),
        settings=Settings(
            openai_api_key="test",
            command_timeout_seconds=30,
            verification_commands=(
                ConfiguredVerificationCommand(
                    id="required-test",
                    command="pytest -q tests/test_app.py",
                    required=True,
                ),
                ConfiguredVerificationCommand(
                    id="optional-test",
                    command="python3 -m unittest discover -v",
                    required=False,
                ),
            ),
        ),
    )
    repository = FakeRepository(
        results=[
            CommandResult(
                command=command.command,
                exit_code=exit_code,
                stdout="",
                stderr="",
            )
            for command, exit_code in zip(commands, (0, 0, 5), strict=True)
        ]
    )

    result = Verifier(
        repository=repository,  # type: ignore[arg-type]
        artifacts=RunArtifacts(tmp_path),
        max_log_chars=4_000,
    ).verify(commands, pass_number=1)

    assert result.status is VerificationStatus.PASS
    assert result.passing_check_count == 2
    assert result.uncertainty == ("Optional verification fail: optional-test",)
    assert result.checks[2].status is VerificationStatus.FAIL
