"""Provider-neutral sandbox interface."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class CommandResult:
    """Captured result of one command inside a repository sandbox."""

    command: str
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False


class Sandbox(Protocol):
    """Small lifecycle and execution contract for repository sandboxes."""

    def start(self) -> None:
        """Start the sandbox."""
        ...

    def exec(
        self,
        command: str,
        *,
        timeout_seconds: int | None = None,
    ) -> CommandResult:
        """Execute a command in the isolated repository workspace."""
        ...

    def stop(self) -> None:
        """Stop and remove the sandbox."""
        ...
