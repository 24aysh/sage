"""Conservative deterministic verification command discovery."""

from __future__ import annotations

import shlex

from sage.config import ConfiguredVerificationCommand
from sage.domain.planning import ExecutionPlan
from sage.domain.verification import VerificationCommand, VerificationSource
from sage.repository.scout import RepositoryMap

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


def discover_verification_commands(
    *,
    repository_map: RepositoryMap,
    plan: ExecutionPlan,
    timeout_seconds: int,
    configured: tuple[ConfiguredVerificationCommand, ...] = (),
) -> tuple[VerificationCommand, ...]:
    """Build at most four sequential sandbox checks in stable order."""

    commands: list[VerificationCommand] = [
        VerificationCommand(
            check_id="git-diff-check",
            command="git diff --check HEAD --",
            source=VerificationSource.MANDATORY,
            required=True,
            timeout_seconds=timeout_seconds,
        )
    ]
    seen = {commands[0].command}
    for item in configured:
        if item.command in seen:
            continue
        commands.append(
            VerificationCommand(
                check_id=item.check_id,
                command=item.command,
                source=VerificationSource.CONFIGURED,
                required=item.required,
                timeout_seconds=min(timeout_seconds, item.timeout_seconds),
            )
        )
        seen.add(item.command)
        if len(commands) >= 4:
            return tuple(commands)
    for index, hint in enumerate(plan.verification_hints, start=1):
        command = _validated_hint(hint.command)
        if command is None or command in seen:
            continue
        commands.append(
            VerificationCommand(
                check_id=f"planned-{index}",
                command=command,
                source=VerificationSource.PLANNED,
                required=hint.required,
                timeout_seconds=timeout_seconds,
            )
        )
        seen.add(command)
        if len(commands) >= 4:
            return tuple(commands)

    paths = set(repository_map.tracked_paths_sample)
    if (
        len(commands) < 4
        and any(path.endswith(".py") for path in paths)
        and any(
            part in {"test", "tests"}
            for path in paths
            for part in path.split("/")
        )
    ):
        command = "python3 -m unittest discover -v"
        if command not in seen:
            commands.append(
                VerificationCommand(
                    check_id="python-unittest",
                    command=command,
                    source=VerificationSource.DISCOVERED,
                    required=False,
                    timeout_seconds=timeout_seconds,
                )
            )
    return tuple(commands[:4])


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
