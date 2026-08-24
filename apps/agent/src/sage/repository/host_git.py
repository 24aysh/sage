"""Trusted host-side Git subprocess execution."""

from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

from sage.errors import HostGitError, HostGitTimeoutError


def run_git(
    arguments: Sequence[str],
    *,
    repository: Path | None = None,
    timeout_seconds: int = 60,
    environment: Mapping[str, str] | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run Git without a shell and return its captured result.

    Callers remain responsible for interpreting non-zero exit codes because
    workspace preparation and publication attach different domain context.
    Environment values are passed to Git but are never included in errors.
    """

    if timeout_seconds < 1:
        raise ValueError("Git timeout must be at least one second.")

    command = ["git"]
    if repository is not None:
        command.extend(["-C", str(repository)])
    command.extend(arguments)

    try:
        return subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=dict(environment) if environment is not None else None,
            input=input_text,
        )
    except FileNotFoundError as error:
        raise HostGitError("Git executable was not found.") from error
    except subprocess.TimeoutExpired as error:
        raise HostGitTimeoutError(
            f"Git command timed out after {timeout_seconds} seconds."
        ) from error
