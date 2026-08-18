"""Bounded repository tree inspection."""

from __future__ import annotations

from pathlib import Path

from sage.errors import RepositoryError
from sage.repository.paths import resolve_workspace_path

DEFAULT_SKIPPED_NAMES = frozenset(
    {
        ".git",
        ".next",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "target",
        "vendor",
    }
)
MAX_TREE_DEPTH = 4
MAX_TREE_ENTRIES = 500


def list_tree(
    workspace_root: Path,
    *,
    path: str = ".",
    max_depth: int = 2,
) -> str:
    """Render a bounded file tree rooted at a validated repository path."""

    if not 0 <= max_depth <= MAX_TREE_DEPTH:
        raise RepositoryError(
            f"Tree depth must be between 0 and {MAX_TREE_DEPTH}."
        )

    root = resolve_workspace_path(workspace_root, path)
    if not root.exists():
        raise RepositoryError(f"Repository path does not exist: {path}")
    if not root.is_dir():
        raise RepositoryError(f"Repository path is not a directory: {path}")

    output: list[str] = []
    truncated = _append_directory(root, 0, max_depth, output)
    if truncated:
        output.append(f"... [tree truncated after {MAX_TREE_ENTRIES} entries]")
    return "\n".join(output) if output else "[empty directory]"


def _append_directory(
    directory: Path,
    depth: int,
    max_depth: int,
    output: list[str],
) -> bool:
    try:
        entries = sorted(
            (entry for entry in directory.iterdir() if entry.name not in DEFAULT_SKIPPED_NAMES),
            key=lambda entry: (not entry.is_dir(), entry.name.casefold()),
        )
    except OSError as error:
        raise RepositoryError(f"Unable to inspect repository directory: {directory}") from error

    for entry in entries:
        if len(output) >= MAX_TREE_ENTRIES:
            return True
        is_directory = entry.is_dir() and not entry.is_symlink()
        suffix = "/" if is_directory else ""
        output.append(f"{'  ' * depth}{entry.name}{suffix}")
        if is_directory and depth < max_depth:
            if _append_directory(entry, depth + 1, max_depth, output):
                return True
    return False
