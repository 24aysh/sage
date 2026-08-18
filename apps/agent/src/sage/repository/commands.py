"""Controlled shell command execution inside the repository sandbox."""

from sage.errors import RepositoryError
from sage.repository.output import truncate_text
from sage.sandbox.base import CommandResult, Sandbox

MAX_COMMAND_CHARS = 4_000


def run_command(
    sandbox: Sandbox,
    *,
    command: str,
    timeout_seconds: int | None,
    default_timeout_seconds: int,
    max_output_chars: int,
) -> CommandResult:
    """Run one bounded command in Docker and bound the returned streams."""

    if not command.strip():
        raise RepositoryError("Command cannot be empty.")
    if len(command) > MAX_COMMAND_CHARS:
        raise RepositoryError(f"Command cannot exceed {MAX_COMMAND_CHARS} characters.")
    if timeout_seconds is not None and timeout_seconds < 1:
        raise RepositoryError("Command timeout must be at least one second.")

    effective_timeout = min(
        timeout_seconds or default_timeout_seconds,
        default_timeout_seconds,
    )
    result = sandbox.exec(command, timeout_seconds=effective_timeout)
    stdout, stderr = _bound_streams(
        result.stdout,
        result.stderr,
        max_output_chars=max_output_chars,
    )
    return CommandResult(
        command=result.command,
        exit_code=result.exit_code,
        stdout=stdout,
        stderr=stderr,
        timed_out=result.timed_out,
    )


def _bound_streams(
    stdout: str,
    stderr: str,
    *,
    max_output_chars: int,
) -> tuple[str, str]:
    if len(stdout) + len(stderr) <= max_output_chars:
        return stdout, stderr
    if stdout and stderr:
        stdout_budget = max_output_chars // 2
        return (
            truncate_text(stdout, stdout_budget),
            truncate_text(stderr, max_output_chars - stdout_budget),
        )
    if stdout:
        return truncate_text(stdout, max_output_chars), ""
    return "", truncate_text(stderr, max_output_chars)
