"""Bounded source-file reading."""

from pathlib import Path

from issue_agent.errors import RepositoryError
from issue_agent.repository.output import truncate_text
from issue_agent.repository.paths import resolve_workspace_path

MAX_READ_LINES = 300


def read_file(
    workspace_root: Path,
    *,
    path: str,
    start_line: int = 1,
    end_line: int | None = None,
    max_output_chars: int,
) -> str:
    """Read a validated, line-numbered source region."""

    if start_line < 1:
        raise RepositoryError("start_line must be at least 1.")
    if end_line is not None and end_line < start_line:
        raise RepositoryError("end_line must be greater than or equal to start_line.")

    requested_end = end_line if end_line is not None else start_line + MAX_READ_LINES - 1
    if requested_end - start_line + 1 > MAX_READ_LINES:
        raise RepositoryError(f"A maximum of {MAX_READ_LINES} lines may be read at once.")

    resolved = resolve_workspace_path(workspace_root, path)
    if not resolved.is_file():
        raise RepositoryError(f"Repository file does not exist: {path}")

    try:
        data = resolved.read_bytes()
    except OSError as error:
        raise RepositoryError(f"Unable to read repository file: {path}") from error
    if b"\x00" in data[:8192]:
        raise RepositoryError(f"Binary repository files cannot be read: {path}")
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise RepositoryError(f"Repository file is not valid UTF-8 text: {path}") from error

    lines = content.splitlines()
    selected = lines[start_line - 1 : requested_end]
    if not selected:
        return f"[no lines in requested range; file has {len(lines)} lines]"
    numbered = "\n".join(
        f"{line_number} | {line}"
        for line_number, line in enumerate(selected, start=start_line)
    )
    return truncate_text(numbered, max_output_chars)
