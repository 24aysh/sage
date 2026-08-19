"""Exact GitHub issue-command parsing."""

from enum import StrEnum


class SageCommand(StrEnum):
    """Semantic actions accepted from GitHub issue comments."""

    SOLVE = "solve"


_COMMANDS = {
    "/sage solve": SageCommand.SOLVE,
    "/sage fix": SageCommand.SOLVE,
}


def parse_command(body: str) -> SageCommand | None:
    """Return a supported command only when the complete body matches exactly."""

    return _COMMANDS.get(body)
