"""Preparation of isolated per-run Git workspaces."""

from __future__ import annotations

import logging
import secrets
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from issue_agent.config import Settings
from issue_agent.domain.requests import PreparedRun, SolveRequest
from issue_agent.errors import WorkspaceError

logger = logging.getLogger(__name__)


def create_run_id() -> str:
    """Create a sortable, collision-resistant local run identifier."""

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{secrets.token_hex(4)}"


def prepare_run(request: SolveRequest, settings: Settings) -> PreparedRun:
    """Clone a committed source revision into a new isolated run directory."""

    source_repo = _validate_source_repository(request.repo_path)
    if not request.issue_path.is_file():
        raise WorkspaceError(f"Issue file does not exist: {request.issue_path}")

    base_sha = _resolve_base_sha(source_repo, request.base_ref)
    run_id, run_dir = _create_run_directory(settings.runs_dir)
    workspace_dir = run_dir / "repo"

    clone = _run_git(
        [
            "git",
            "clone",
            "--no-hardlinks",
            "--no-checkout",
            "--",
            str(source_repo),
            str(workspace_dir),
        ]
    )
    if clone.returncode != 0:
        raise WorkspaceError(_git_failure("Unable to clone source repository", clone))

    checkout = _run_git(
        ["git", "-C", str(workspace_dir), "checkout", "--detach", base_sha]
    )
    if checkout.returncode != 0:
        raise WorkspaceError(_git_failure("Unable to check out base revision", checkout))

    cloned_sha = _run_git(
        ["git", "-C", str(workspace_dir), "rev-parse", "--verify", "HEAD"]
    )
    if cloned_sha.returncode != 0:
        raise WorkspaceError(_git_failure("Unable to resolve cloned base SHA", cloned_sha))

    prepared = PreparedRun(
        run_id=run_id,
        source_repo=source_repo,
        run_dir=run_dir.resolve(),
        workspace_dir=workspace_dir.resolve(),
        base_ref=request.base_ref,
        base_sha=cloned_sha.stdout.strip(),
    )
    logger.info(
        "workspace prepared",
        extra={"run_id": run_id, "base_sha": prepared.base_sha},
    )
    return prepared


def _validate_source_repository(requested_path: Path) -> Path:
    path = requested_path.expanduser().resolve()
    if not path.is_dir():
        raise WorkspaceError(f"Repository path does not exist: {path}")

    result = _run_git(["git", "-C", str(path), "rev-parse", "--show-toplevel"])
    if result.returncode != 0:
        raise WorkspaceError(f"Path is not a Git repository: {path}")
    return Path(result.stdout.strip()).resolve()


def _resolve_base_sha(source_repo: Path, base_ref: str) -> str:
    if not base_ref.strip():
        raise WorkspaceError("Base ref cannot be empty.")

    result = _run_git(
        [
            "git",
            "-C",
            str(source_repo),
            "rev-parse",
            "--verify",
            "--end-of-options",
            f"{base_ref}^{{commit}}",
        ]
    )
    if result.returncode != 0:
        raise WorkspaceError(f"Base ref does not resolve to a commit: {base_ref}")
    return result.stdout.strip()


def _create_run_directory(runs_dir: Path) -> tuple[str, Path]:
    try:
        root = runs_dir.expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        for _ in range(5):
            run_id = create_run_id()
            run_dir = root / run_id
            try:
                run_dir.mkdir()
            except FileExistsError:
                continue
            return run_id, run_dir
    except OSError as error:
        raise WorkspaceError(f"Unable to create runs directory: {runs_dir}") from error
    raise WorkspaceError("Unable to allocate a unique run directory.")


def _run_git(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except FileNotFoundError as error:
        raise WorkspaceError("Git executable was not found.") from error
    except subprocess.TimeoutExpired as error:
        raise WorkspaceError("Git workspace preparation timed out.") from error


def _git_failure(message: str, result: subprocess.CompletedProcess[str]) -> str:
    detail = result.stderr.strip() or result.stdout.strip() or "unknown Git error"
    return f"{message}: {detail}"
