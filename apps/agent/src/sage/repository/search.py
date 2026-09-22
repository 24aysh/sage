"""Exact repository text search through the sandbox."""

from __future__ import annotations

import shlex
import json
from pathlib import Path

from sage.errors import CommandTimeoutError, RepositoryError
from sage.domain.navigation import SearchMatch, SearchResult
from sage.repository.output import truncate_text
from sage.repository.filesystem import workspace_relative_path
from sage.repository.selection import IGNORED_GLOBS
from sage.sandbox.base import Sandbox

MAX_SEARCH_RESULTS = 100
def search_text(
    workspace_root: Path,
    sandbox: Sandbox,
    *,
    query: str,
    path: str = ".",
    max_results: int = 50,
    max_output_chars: int,
    timeout_seconds: int,
    structured: bool = False,
) -> str | SearchResult:
    """Search for a literal text query and return bounded source locations."""

    if not query:
        raise RepositoryError("Search query cannot be empty.")
    if not 1 <= max_results <= MAX_SEARCH_RESULTS:
        raise RepositoryError(
            f"max_results must be between 1 and {MAX_SEARCH_RESULTS}."
        )

    relative_path = workspace_relative_path(workspace_root, path)
    glob_arguments = " ".join(
        f"--glob {shlex.quote(f'!{pattern}')}" for pattern in IGNORED_GLOBS
    )
    command = (
        "rg --line-number --column --color never --hidden --fixed-strings "
        + ("--json " if structured else "") +
        f"{glob_arguments} -e {shlex.quote(query)} {shlex.quote(relative_path)}"
    )
    result = sandbox.exec(command, timeout_seconds=timeout_seconds)
    if result.timed_out:
        raise CommandTimeoutError("Repository search timed out.")
    if result.exit_code == 1:
        return SearchResult(text="[no matches]") if structured else "[no matches]"
    if result.exit_code != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RepositoryError(f"Repository search failed: {detail}")

    if structured:
        return _structured_result(result.stdout, max_results, max_output_chars,
                                  include_path=not (workspace_root / relative_path).is_file())
    matches = result.stdout.splitlines()
    bounded_matches = matches[:max_results]
    output = "\n".join(bounded_matches)
    if len(matches) > max_results:
        output += f"\n... [results truncated after {max_results} matches]"
    return truncate_text(output, max_output_chars)


def _structured_result(raw: str, limit: int, chars: int, *, include_path: bool = True) -> SearchResult:
    """Only complete rg records can authorize follow-up reads; never split on colons."""
    found = []
    displayed = []
    incomplete = False
    for line in raw.splitlines():
        try:
            event = json.loads(line)
            if event.get("type") != "match":
                continue
            data = event["data"]
            # rg uses base64 for undecodable paths/text. Leave these to the Solver.
            match = SearchMatch(path=data["path"]["text"].removeprefix("./"),
                line=data["line_number"], column=data["submatches"][0]["start"] + 1,
                text=data["lines"]["text"].rstrip("\r\n"))
            found.append(match)
            prefix = data["path"]["text"] + ":" if include_path else ""
            displayed.append(f"{prefix}{match.line}:{match.column}:{match.text}")
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            incomplete = True
    displayed = displayed[:limit]
    output = "\n".join(displayed)
    if len(found) > limit:
        output += f"\n... [results truncated after {limit} matches]"
    if incomplete:
        output += "\n[search output incomplete; navigation unavailable]"
    text = truncate_text(output or "[no matches]", chars)
    # A partial displayed record is not a visible source anchor.
    visible = tuple(m for m, row in zip(found[:limit], displayed) if row in text)
    return SearchResult(text=text, matches=() if incomplete else visible,
                        truncated=incomplete or len(found) > limit or len(output) > chars)
