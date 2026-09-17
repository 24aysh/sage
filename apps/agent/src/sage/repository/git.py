"""Authoritative Git status and diff operations."""

from __future__ import annotations

import shlex

from sage.errors import CommandExecutionError, CommandTimeoutError, RepositoryError
from sage.repository.output import truncate_text
from sage.repository.selection import IGNORED_UNTRACKED_PATHSPECS
from sage.sandbox.base import CommandResult, Sandbox


def show_diff(
    sandbox: Sandbox,
    *,
    max_output_chars: int,
    timeout_seconds: int,
) -> str:
    """Return bounded status, diff statistics, and textual diff."""

    _ensure_untracked_files_are_diffable(sandbox, timeout_seconds)
    status = _required_command(
        sandbox,
        "git status --short --untracked-files=all",
        timeout_seconds,
    )
    stat = _required_command(
        sandbox,
        "git diff --stat --no-ext-diff HEAD --",
        timeout_seconds,
    )
    diff = _required_command(
        sandbox,
        "git diff --no-ext-diff HEAD --",
        timeout_seconds,
    )
    rendered = (
        f"Status:\n{status.stdout or '[clean]'}\n"
        f"Diff stat:\n{stat.stdout or '[no diff]'}\n"
        f"Diff:\n{diff.stdout or '[no diff]'}"
    )
    return truncate_text(rendered, max_output_chars)


def list_branches(
    sandbox: Sandbox,
    *,
    max_output_chars: int,
    timeout_seconds: int,
) -> str:
    """Return bounded local and remote-tracking branch details."""

    result = _required_command(
        sandbox,
        "git branch --all --verbose --no-abbrev --no-color",
        timeout_seconds,
    )
    return truncate_text(result.stdout or "[no branches]", max_output_chars)


def switch_branch(
    sandbox: Sandbox,
    *,
    branch_name: str,
    timeout_seconds: int,
) -> str:
    """Switch to one existing branch without carrying uncommitted changes."""

    branch = branch_name.strip()
    if not branch:
        raise RepositoryError("Branch name cannot be empty.")
    if len(branch) > 255:
        raise RepositoryError("Branch name cannot exceed 255 characters.")

    quoted_branch = shlex.quote(branch)
    _required_command(
        sandbox,
        f"git check-ref-format {shlex.quote(f'refs/heads/{branch}')}",
        timeout_seconds,
    )
    status = _required_command(
        sandbox,
        "git status --porcelain --untracked-files=all",
        timeout_seconds,
    )
    if status.stdout:
        raise RepositoryError(
            "Cannot switch branches while the sandbox worktree has uncommitted changes."
        )

    _required_command(
        sandbox,
        f"git switch -- {quoted_branch}",
        timeout_seconds,
    )
    current = _required_command(
        sandbox,
        "git branch --show-current",
        timeout_seconds,
    ).stdout.strip()
    return f"Switched to branch {current or branch}."


def get_complete_diff(sandbox: Sandbox, *, timeout_seconds: int) -> str:
    """Return the complete binary-capable diff against the prepared base."""

    _ensure_untracked_files_are_diffable(sandbox, timeout_seconds)
    result = _required_command(
        sandbox,
        "git diff --binary --no-ext-diff HEAD --",
        timeout_seconds,
    )
    return result.stdout


def get_changed_files(sandbox: Sandbox, *, timeout_seconds: int) -> list[str]:
    """Return the actual changed-file list derived from Git."""

    _ensure_untracked_files_are_diffable(sandbox, timeout_seconds)
    result = _required_command(
        sandbox,
        "git diff --name-only -z --no-ext-diff HEAD --",
        timeout_seconds,
    )
    return sorted(path for path in result.stdout.split("\0") if path)


def get_head_sha(sandbox: Sandbox, *, timeout_seconds: int) -> str:
    """Return the current workspace HEAD object ID."""

    result = _required_command(sandbox, "git rev-parse HEAD", timeout_seconds)
    return result.stdout.strip()


def _ensure_untracked_files_are_diffable(
    sandbox: Sandbox,
    timeout_seconds: int,
) -> None:
    # Intent-to-add records no file content; it only lets `git diff HEAD` include
    # new files in the authoritative patch without staging the candidate change.
    ignored_untracked = " ".join(
        shlex.quote(pathspec) for pathspec in IGNORED_UNTRACKED_PATHSPECS
    )
    _required_command(
        sandbox,
        f"git add --intent-to-add --all -- . {ignored_untracked}",
        timeout_seconds,
    )


def _required_command(
    sandbox: Sandbox,
    command: str,
    timeout_seconds: int,
) -> CommandResult:
    result = sandbox.exec(command, timeout_seconds=timeout_seconds)
    if result.timed_out:
        raise CommandTimeoutError(f"Repository command timed out: {command}")
    if result.exit_code != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise CommandExecutionError(f"Repository command failed: {command}: {detail}")
    return result
