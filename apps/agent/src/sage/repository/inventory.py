"""Structured, read-only repository inventory operations for V2."""

from __future__ import annotations

import json
import shlex
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict, Field

from sage.errors import RepositoryError
from sage.repository.host_git import run_git
from sage.repository.selection import IGNORED_GLOBS
from sage.sandbox.base import Sandbox

MAX_TRACKED_PATH_BYTES = 4_000_000
MAX_TRACKED_PATHS = 5_000


class TrackedInventory(BaseModel):
    """Bounded tracked-path inventory and exact total count."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    total_count: int = Field(ge=0)
    paths: tuple[str, ...] = Field(max_length=MAX_TRACKED_PATHS)
    truncated: bool


class LiteralMatch(BaseModel):
    """One bounded literal ripgrep match."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    query: str = Field(min_length=1, max_length=300)
    path: str = Field(min_length=1, max_length=1_000)
    line: int = Field(ge=1)
    text: str = Field(max_length=1_000)


def tracked_inventory(workspace: Path) -> TrackedInventory:
    """Read a deterministic bounded tracked-file list from Git."""

    result = run_git(
        ["ls-files", "-z", "--cached"],
        repository=workspace,
        timeout_seconds=60,
    )
    if result.returncode != 0:
        raise RepositoryError("Unable to enumerate repository files with Git.")
    encoded_size = len(result.stdout.encode("utf-8", errors="replace"))
    if encoded_size > MAX_TRACKED_PATH_BYTES:
        raise RepositoryError("Repository tracked-path inventory exceeds the V2 cap.")
    all_paths = sorted(path for path in result.stdout.split("\0") if path)
    safe_paths = tuple(path for path in all_paths if _safe_inventory_path(path))
    return TrackedInventory(
        total_count=len(all_paths),
        paths=safe_paths[:MAX_TRACKED_PATHS],
        truncated=len(safe_paths) > MAX_TRACKED_PATHS,
    )


def search_literal_matches(
    sandbox: Sandbox,
    *,
    query: str,
    max_results: int,
    timeout_seconds: int,
) -> tuple[LiteralMatch, ...]:
    """Search repository text through ripgrep's machine-readable JSON format."""

    if not query or len(query) > 300:
        raise RepositoryError("Scout query must contain 1 to 300 characters.")
    if not 1 <= max_results <= 40:
        raise RepositoryError("Scout result limit must be between 1 and 40.")
    globs = " ".join(
        f"--glob {shlex.quote(f'!{pattern}')}" for pattern in IGNORED_GLOBS
    )
    command = (
        "rg --json --hidden --fixed-strings --color never "
        f"{globs} -e {shlex.quote(query)} ."
    )
    result = sandbox.exec(command, timeout_seconds=timeout_seconds)
    if result.timed_out:
        raise RepositoryError("Repository Scout search timed out.")
    if result.exit_code == 1:
        return ()
    if result.exit_code != 0:
        raise RepositoryError("Repository Scout search failed.")

    matches: list[LiteralMatch] = []
    for line in result.stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "match":
            continue
        data = event.get("data", {})
        path = str(data.get("path", {}).get("text", "")).removeprefix("./")
        if not _safe_inventory_path(path):
            continue
        text = str(data.get("lines", {}).get("text", "")).rstrip("\r\n")[:1_000]
        matches.append(
            LiteralMatch(
                query=query,
                path=path,
                line=max(1, int(data.get("line_number", 1))),
                text=text,
            )
        )
        if len(matches) >= max_results:
            break
    return tuple(matches)


def _safe_inventory_path(path: str) -> bool:
    if not path or "\x00" in path:
        return False
    pure = PurePosixPath(path)
    if pure.is_absolute() or ".." in pure.parts or ".git" in pure.parts:
        return False
    ignored_roots = {pattern.split("/", 1)[0] for pattern in IGNORED_GLOBS}
    return not pure.parts or pure.parts[0] not in ignored_roots
