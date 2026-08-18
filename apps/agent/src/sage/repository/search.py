"""Exact repository text search through the sandbox."""

from __future__ import annotations

import shlex
from pathlib import Path

from sage.errors import CommandTimeoutError, RepositoryError
from sage.repository.output import truncate_text
from sage.repository.paths import workspace_relative_path
from sage.sandbox.base import Sandbox

MAX_SEARCH_RESULTS = 100
_IGNORED_GLOBS = (
    ".git/**",
    "node_modules/**",
    ".next/**",
    "dist/**",
    "build/**",
    "target/**",
    "vendor/**",
    "__pycache__/**",
    ".venv/**",
)


def search_text(
    workspace_root: Path,
    sandbox: Sandbox,
    *,
    query: str,
    path: str = ".",
    max_results: int = 50,
    max_output_chars: int,
    timeout_seconds: int,
) -> str:
    """Search for a literal text query and return bounded source locations."""

    if not query:
        raise RepositoryError("Search query cannot be empty.")
    if not 1 <= max_results <= MAX_SEARCH_RESULTS:
        raise RepositoryError(
            f"max_results must be between 1 and {MAX_SEARCH_RESULTS}."
        )

    relative_path = workspace_relative_path(workspace_root, path)
    glob_arguments = " ".join(
        f"--glob {shlex.quote(f'!{pattern}')}" for pattern in _IGNORED_GLOBS
    )
    command = (
        "rg --line-number --column --color never --hidden --fixed-strings "
        f"{glob_arguments} -e {shlex.quote(query)} {shlex.quote(relative_path)}"
    )
    result = sandbox.exec(command, timeout_seconds=timeout_seconds)
    if result.timed_out:
        raise CommandTimeoutError("Repository search timed out.")
    if result.exit_code == 1:
        return "[no matches]"
    if result.exit_code != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RepositoryError(f"Repository search failed: {detail}")

    matches = result.stdout.splitlines()
    bounded_matches = matches[:max_results]
    output = "\n".join(bounded_matches)
    if len(matches) > max_results:
        output += f"\n... [results truncated after {max_results} matches]"
    return truncate_text(output, max_output_chars)
