"""Authoritative Git status and diff operations."""

from __future__ import annotations

import shlex

from sage.errors import CommandExecutionError, CommandTimeoutError
from sage.repository.output import truncate_text
from sage.repository.selection import IGNORED_NAMES
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


def _ensure_untracked_files_are_diffable(
    sandbox: Sandbox,
    timeout_seconds: int,
) -> None:
    # Intent-to-add records no file content; it only lets `git diff HEAD` include
    # new files in the authoritative patch without staging the candidate change.
    ignored_untracked = " ".join(
        shlex.quote(f":(exclude,glob)**/{name}/**")
        for name in sorted(IGNORED_NAMES)
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
