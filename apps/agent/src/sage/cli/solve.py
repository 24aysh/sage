"""Local solve arguments and prerequisite checks."""

from __future__ import annotations

import argparse
import asyncio
import shutil
import subprocess
from pathlib import Path

from sage.cli.memory import _memory_service
from sage.cli.output import _render_result
from sage.composition import build_orchestrator
from sage.config import Settings
from sage.domain.solve import SolveOutcome, SolveRequest
from sage.errors import ConfigurationError
from sage.workflows.solve import solve_issue


def add_parser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    solve_parser = subparsers.add_parser("solve", help="Run the issue solver.")
    solve_parser.add_argument("--repo", required=True, type=Path)
    solve_parser.add_argument("--issue-file", required=True, type=Path)
    solve_parser.add_argument("--base-ref", default="HEAD")
    solve_parser.add_argument("--sandbox-image")
    solve_parser.add_argument("--memory-file", type=Path)
    solve_parser.add_argument("--debug", action="store_true")
    solve_parser.set_defaults(handler=_run_local_solve)
    return solve_parser


def _run_local_solve(arguments: argparse.Namespace) -> int:
    """Run one local solve."""

    settings = Settings.from_env()
    request = SolveRequest(
        repo_path=arguments.repo.expanduser().resolve(),
        issue_path=arguments.issue_file.expanduser().resolve(),
        base_ref=arguments.base_ref,
        sandbox_image=arguments.sandbox_image,
        memory_file=(
            arguments.memory_file.expanduser().resolve()
            if arguments.memory_file is not None
            else None
        ),
    )
    effective_image = request.sandbox_image or settings.sandbox_image
    _validate_prerequisites(request, settings, sandbox_image=effective_image)
    orchestrator = build_orchestrator(settings)
    if request.memory_file is not None:
        result = asyncio.run(
            solve_issue(
                request,
                orchestrator,
                settings,
                memory_service=_memory_service(arguments),
                on_interrupted=lambda partial: _render_result(partial, model=settings.solver_model),
            )
        )
    else:
        result = asyncio.run(solve_issue(request, orchestrator, settings,
            on_interrupted=lambda partial: _render_result(partial, model=settings.solver_model)))
    _render_result(
        result,
        model=settings.solver_model,
    )
    return (
        0
        if result.outcome is SolveOutcome.COMPLETED and result.diff.strip()
        else 2
    )


def _validate_prerequisites(
    request: SolveRequest,
    settings: Settings,
    *,
    sandbox_image: str,
) -> None:
    if not request.repo_path.is_dir():
        raise ConfigurationError(f"Repository path does not exist: {request.repo_path}")
    if not request.issue_path.is_file():
        raise ConfigurationError(f"Issue file does not exist: {request.issue_path}")
    if shutil.which("git") is None:
        raise ConfigurationError("Git executable was not found.")
    if shutil.which("docker") is None:
        raise ConfigurationError("Docker executable was not found.")

    _check_docker_command(
        ["docker", "info", "--format", "{{.ServerVersion}}"],
        failure_message="Docker daemon is not reachable.",
    )
    _check_docker_command(
        ["docker", "image", "inspect", sandbox_image],
        failure_message=f"Docker sandbox image does not exist: {sandbox_image}",
    )
    try:
        settings.runs_dir.expanduser().mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ConfigurationError(
            f"Runs directory cannot be created: {settings.runs_dir}"
        ) from error


def _check_docker_command(command: list[str], *, failure_message: str) -> None:
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        raise ConfigurationError(failure_message) from error
    if result.returncode != 0:
        raise ConfigurationError(failure_message)
