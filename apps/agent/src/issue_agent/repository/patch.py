"""Validated unified-diff application."""

from __future__ import annotations

import os
import shlex
import tempfile
from pathlib import Path, PurePosixPath

from issue_agent.errors import PatchError, PathSafetyError
from issue_agent.repository.output import truncate_text
from issue_agent.repository.paths import resolve_workspace_path
from issue_agent.sandbox.base import Sandbox


def apply_patch(
    workspace_root: Path,
    sandbox: Sandbox,
    *,
    patch: str,
    max_output_chars: int,
    timeout_seconds: int,
) -> str:
    """Validate patch targets and apply the patch inside Docker."""

    if not patch.strip():
        raise PatchError("Patch cannot be empty.")
    paths = _extract_patch_paths(patch)
    if not paths:
        raise PatchError("Patch does not contain recognizable file paths.")
    for path in paths:
        _validate_patch_path(workspace_root, path)

    git_dir = workspace_root / ".git"
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="issue-agent-patch-",
            suffix=".diff",
            dir=git_dir,
        )
    except OSError as error:
        raise PatchError("Unable to create a temporary patch file.") from error
    temporary_path = Path(temporary_name)
    try:
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as patch_file:
                patch_file.write(patch)
        except OSError as error:
            raise PatchError("Unable to write the temporary patch file.") from error
        container_path = f"/workspace/.git/{temporary_path.name}"
        result = sandbox.exec(
            f"git apply --whitespace=nowarn {shlex.quote(container_path)}",
            timeout_seconds=timeout_seconds,
        )
    finally:
        temporary_path.unlink(missing_ok=True)

    if result.timed_out:
        raise PatchError("Patch application timed out.")
    if result.exit_code != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise PatchError(f"Patch could not be applied: {detail}")
    output = result.stdout.strip() or "Patch applied successfully."
    return truncate_text(output, max_output_chars)


def _extract_patch_paths(patch: str) -> set[str]:
    paths: set[str] = set()
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            try:
                parts = shlex.split(line)
            except ValueError as error:
                raise PatchError("Patch contains an invalid diff header.") from error
            if len(parts) < 4:
                raise PatchError("Patch contains an incomplete diff header.")
            paths.update((parts[2], parts[3]))
        elif line.startswith(("--- ", "+++ ")):
            value = line[4:].split("\t", maxsplit=1)[0].strip()
            if value.startswith('"'):
                try:
                    parsed = shlex.split(value)
                except ValueError as error:
                    raise PatchError("Patch contains an invalid file header.") from error
                if parsed:
                    value = parsed[0]
            paths.add(value)
    return {path for path in paths if path != "/dev/null"}


def _validate_patch_path(workspace_root: Path, patch_path: str) -> None:
    normalized = patch_path
    if normalized.startswith(("a/", "b/")):
        normalized = normalized[2:]
    pure_path = PurePosixPath(normalized)
    if pure_path.is_absolute() or ".." in pure_path.parts:
        raise PatchError(f"Patch path is outside the workspace: {patch_path}")
    if ".git" in pure_path.parts:
        raise PatchError("Patches cannot modify Git internals.")
    try:
        resolve_workspace_path(workspace_root, pure_path.as_posix())
    except PathSafetyError as error:
        raise PatchError(f"Unsafe patch path: {patch_path}") from error
