"""Shared repository path validation."""

from pathlib import Path

from issue_agent.errors import PathSafetyError


def resolve_workspace_path(workspace_root: Path, requested_path: str) -> Path:
    """Resolve a repository-relative path without allowing workspace escape."""

    root = workspace_root.resolve(strict=True)
    relative = Path(requested_path)
    if relative.is_absolute():
        raise PathSafetyError(f"Absolute repository paths are not allowed: {requested_path}")
    if ".git" in relative.parts:
        raise PathSafetyError("Access to Git internals is not allowed.")

    resolved = (root / relative).resolve(strict=False)
    if not resolved.is_relative_to(root):
        raise PathSafetyError(f"Repository path escapes the workspace: {requested_path}")
    resolved_relative = resolved.relative_to(root)
    if resolved_relative.parts and resolved_relative.parts[0] == ".git":
        raise PathSafetyError("Access to Git internals is not allowed.")
    return resolved


def workspace_relative_path(workspace_root: Path, requested_path: str) -> str:
    """Return a validated path relative to the workspace using POSIX separators."""

    resolved = resolve_workspace_path(workspace_root, requested_path)
    relative = resolved.relative_to(workspace_root.resolve(strict=True))
    return relative.as_posix() or "."
