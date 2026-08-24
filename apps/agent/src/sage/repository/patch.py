"""Validated unified-diff application."""

from __future__ import annotations

import hashlib
import logging
import os
import shlex
import tempfile
from pathlib import Path, PurePosixPath

from sage.errors import PatchError, PathSafetyError
from sage.repository.output import truncate_text
from sage.repository.paths import resolve_workspace_path
from sage.sandbox.base import Sandbox

logger = logging.getLogger(__name__)


def apply_patch(
    workspace_root: Path,
    sandbox: Sandbox,
    *,
    patch: str,
    max_output_chars: int,
    timeout_seconds: int,
) -> str:
    """Validate patch targets and apply the patch inside Docker."""

    patch = normalize_null_file_headers(patch)
    if not patch.strip():
        raise PatchError("Patch cannot be empty.")
    paths = _extract_patch_paths(patch)
    if not paths:
        raise PatchError("Patch does not contain recognizable file paths.")
    for path in paths:
        _validate_patch_path(workspace_root, path)
    logical_path_count = len({_strip_git_prefix(path) for path in paths})
    patch_digest = hashlib.sha256(patch.encode("utf-8")).hexdigest()[:12]
    logger.info(
        "Patch: applying files=%d lines=%d digest=%s recount=true",
        logical_path_count,
        len(patch.splitlines()),
        patch_digest,
    )

    git_dir = workspace_root / ".git"
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="sage-patch-",
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
            "git apply --whitespace=nowarn --recount "
            f"{shlex.quote(container_path)}",
            timeout_seconds=timeout_seconds,
        )
    finally:
        temporary_path.unlink(missing_ok=True)

    if result.timed_out:
        logger.warning("Patch: finished status=timeout digest=%s", patch_digest)
        raise PatchError("Patch application timed out.")
    if result.exit_code != 0:
        detail = _safe_patch_diagnostic(
            result.stderr.strip() or result.stdout.strip()
        )
        logger.warning(
            "Patch: finished status=rejected digest=%s reason=%s",
            patch_digest,
            detail,
        )
        raise PatchError(f"Patch could not be applied: {detail}")
    logger.info("Patch: finished status=applied digest=%s", patch_digest)
    output = result.stdout.strip() or "Patch applied successfully."
    return truncate_text(output, max_output_chars)


def normalize_null_file_headers(patch: str) -> str:
    """Canonicalize a bare dev/null file header without changing patch content."""

    normalized = patch.replace("\r\n", "\n").replace("\r", "\n")
    rendered: list[str] = []
    for raw_line in normalized.splitlines(keepends=True):
        line = raw_line.removesuffix("\n")
        ending = "\n" if raw_line.endswith("\n") else ""
        if line.startswith(("--- ", "+++ ")):
            prefix = line[:4]
            value, separator, metadata = line[4:].partition("\t")
            if value.strip() == "dev/null":
                line = f"{prefix}/dev/null"
                if separator:
                    line = f"{line}\t{metadata}"
        rendered.append(f"{line}{ending}")
    return "".join(rendered)


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
    normalized = _strip_git_prefix(patch_path)
    pure_path = PurePosixPath(normalized)
    if pure_path.is_absolute() or ".." in pure_path.parts:
        raise PatchError(f"Patch path is outside the workspace: {patch_path}")
    if ".git" in pure_path.parts:
        raise PatchError("Patches cannot modify Git internals.")
    try:
        resolve_workspace_path(workspace_root, pure_path.as_posix())
    except PathSafetyError as error:
        raise PatchError(f"Unsafe patch path: {patch_path}") from error


def _strip_git_prefix(patch_path: str) -> str:
    if patch_path.startswith(("a/", "b/")):
        return patch_path[2:]
    return patch_path


def _safe_patch_diagnostic(value: str) -> str:
    normalized = "".join(
        character if ord(character) >= 32 and ord(character) != 127 else " "
        for character in value
    )
    return truncate_text(" ".join(normalized.split()), 1_000) or "unknown Git error"
