"""Command-line entrypoint for the local V0 issue solver."""

from __future__ import annotations

import argparse
import asyncio
import logging
import shutil
import subprocess
import sys
from pathlib import Path

from issue_agent.config import Settings
from issue_agent.domain.requests import SolveRequest
from issue_agent.domain.results import SolveResult
from issue_agent.errors import ConfigurationError, IssueAgentError
from issue_agent.runtimes.openai_agents import OpenAIAgentsRuntime
from issue_agent.workflow import solve_issue

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    """Run the IssueAgent CLI and return a documented process exit code."""

    parser = _build_parser()
    arguments = parser.parse_args(argv)
    _configure_logging(debug=arguments.debug)

    try:
        settings = Settings.from_env()
        request = SolveRequest(
            repo_path=arguments.repo.expanduser().resolve(),
            issue_path=arguments.issue_file.expanduser().resolve(),
            base_ref=arguments.base_ref,
            sandbox_image=arguments.sandbox_image,
        )
        effective_image = request.sandbox_image or settings.sandbox_image
        _validate_prerequisites(request, settings, sandbox_image=effective_image)
        runtime = OpenAIAgentsRuntime(settings)
        result = asyncio.run(solve_issue(request, runtime, settings))
    except IssueAgentError as error:
        if arguments.debug:
            logger.exception("IssueAgent failed")
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("ERROR: Interrupted.", file=sys.stderr)
        return 1
    except Exception as error:
        if arguments.debug:
            logger.exception("Unexpected IssueAgent failure")
        print(f"ERROR: Unexpected failure: {error}", file=sys.stderr)
        return 1

    _render_result(result, model=settings.openai_model)
    return 0 if result.diff.strip() else 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="issue-agent",
        description="Solve a written issue in an isolated local repository clone.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    solve_parser = subparsers.add_parser(
        "solve",
        help="Run the V0 single-agent issue solver.",
    )
    solve_parser.add_argument("--repo", required=True, type=Path)
    solve_parser.add_argument("--issue-file", required=True, type=Path)
    solve_parser.add_argument("--base-ref", default="HEAD")
    solve_parser.add_argument("--sandbox-image")
    solve_parser.add_argument("--debug", action="store_true")
    return parser


def _configure_logging(*, debug: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
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


def _render_result(result: SolveResult, *, model: str) -> None:
    print("IssueAgent V0")
    print()
    print(f"Run: {result.run_id}")
    print(f"Base: {result.base_sha[:12]}")
    print(f"Model: {model}")
    print()
    if result.changed_files:
        print("Changed files:")
        for path in result.changed_files:
            print(f"  {path}")
        print()
        print("Summary:")
        print(f"  {result.summary}")
        print()
        print("Workspace:")
        print(f"  {result.workspace_dir}")
        print()
        print("Patch:")
        print(f"  {result.run_dir / 'diff.patch'}")
        return

    print("Agent completed without producing a repository change.")
    print()
    print("Summary:")
    print(f"  {result.summary}")
    if result.remaining_uncertainty:
        print()
        print("Remaining uncertainty:")
        for uncertainty in result.remaining_uncertainty:
            print(f"  {uncertainty}")
    print()
    print("Run artifacts:")
    print(f"  {result.run_dir}")


if __name__ == "__main__":
    raise SystemExit(main())
