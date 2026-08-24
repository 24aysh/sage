"""Conservative deterministic verification command discovery."""

from __future__ import annotations

import shlex

from sage.config import Settings
from sage.domain.solver import SolverPlan
from sage.domain.verification import VerificationCommand, VerificationSource

_ALLOWED_PREFIXES = (
    "python ",
    "python3 ",
    "pytest",
    "npm test",
    "npm run test",
    "npm run lint",
    "make test",
    "make check",
    "cargo test",
    "go test",
)


def discover_solver_verification_commands(
    *,
    plan: SolverPlan,
    settings: Settings,
) -> tuple[VerificationCommand, ...]:
    """Build V2 checks from trusted configuration and Solver plan hints."""

    commands: list[VerificationCommand] = [
        VerificationCommand(
            check_id="git-diff-check",
            command="git diff --check HEAD --",
            source=VerificationSource.MANDATORY,
            required=True,
            timeout_seconds=settings.command_timeout_seconds,
        )
    ]
    seen = {commands[0].command}
    for item in settings.verification_commands:
        if item.command in seen:
            continue
        commands.append(
            VerificationCommand(
                check_id=item.check_id,
                command=item.command,
                source=VerificationSource.CONFIGURED,
                required=item.required,
                timeout_seconds=min(
                    settings.command_timeout_seconds,
                    item.timeout_seconds,
                ),
            )
        )
        seen.add(item.command)
        if len(commands) >= 4:
            return tuple(commands)
    for index, hint in enumerate(plan.verification_commands, start=1):
        command = _validated_hint(hint)
        if command is None or command in seen:
            continue
        commands.append(
            VerificationCommand(
                check_id=f"solver-plan-{index}",
                command=command,
                source=VerificationSource.PLANNED,
                required=False,
                timeout_seconds=settings.command_timeout_seconds,
            )
        )
        seen.add(command)
        if len(commands) >= 4:
            break
    return tuple(commands)


def _validated_hint(command: str) -> str | None:
    normalized = command.strip()
    if not normalized or "\n" in normalized or "\r" in normalized:
        return None
    try:
        shlex.split(normalized)
    except ValueError:
        return None
    if any(token in normalized for token in ("git push", "git commit", "git reset", "git clean")):
        return None
    return normalized if normalized.startswith(_ALLOWED_PREFIXES) else None


def is_allowed_solver_verification_command(command: str) -> bool:
    """Return whether an untrusted Solver command is verification-only."""

    normalized = command.strip()
    return normalized == "git diff --check HEAD --" or _validated_hint(normalized) is not None
