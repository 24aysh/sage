"""Deterministic UTF-8 repository file mutations for tool-driven agents."""

from __future__ import annotations

import os
import stat
import tempfile
from enum import StrEnum
from pathlib import Path

from sage.errors import RepositoryError
from sage.repository.paths import resolve_workspace_path

MAX_EDIT_CHARS = 200_000


class WriteMode(StrEnum):
    """Explicit overwrite policy for ``write_file``."""

    CREATE = "create"
    REPLACE = "replace"
    CREATE_OR_REPLACE = "create_or_replace"


def replace_text(
    workspace_root: Path,
    *,
    path: str,
    old_text: str,
    new_text: str,
    expected_occurrences: int = 1,
) -> str:
    """Replace an exact text value only when its occurrence count is expected."""

    if not old_text:
        raise RepositoryError("old_text must not be empty.")
    if expected_occurrences < 1:
        raise RepositoryError("expected_occurrences must be at least one.")
    _validate_text_size(old_text, name="old_text")
    _validate_text_size(new_text, name="new_text")
    target = _existing_regular_file(workspace_root, path)
    content = _read_utf8(target, path)
    occurrences = content.count(old_text)
    if occurrences != expected_occurrences:
        raise RepositoryError(
            f"Expected {expected_occurrences} occurrence(s) in {path}, found "
            f"{occurrences}; file was not changed."
        )
    updated = content.replace(old_text, new_text)
    _validate_text_size(updated, name="resulting file")
    _write_atomic(target, updated)
    return f"Replaced {occurrences} occurrence(s) in {path}."


def write_file(
    workspace_root: Path,
    *,
    path: str,
    content: str,
    mode: WriteMode,
) -> str:
    """Create or replace one UTF-8 file under an explicit mode policy."""

    _validate_text_size(content, name="content")
    target = resolve_workspace_path(workspace_root, path)
    exists = target.exists()
    if exists and not target.is_file():
        raise RepositoryError(f"Repository path is not a regular file: {path}")
    if mode is WriteMode.CREATE and exists:
        raise RepositoryError(f"Repository file already exists: {path}")
    if mode is WriteMode.REPLACE and not exists:
        raise RepositoryError(f"Repository file does not exist: {path}")
    target.parent.mkdir(parents=True, exist_ok=True)
    # Re-resolve after creating parents so a concurrently introduced symlink
    # cannot redirect the write outside the prepared workspace.
    target = resolve_workspace_path(workspace_root, path)
    _write_atomic(target, content)
    action = "Created" if not exists else "Replaced"
    return f"{action} {path}."


def delete_file(workspace_root: Path, *, path: str) -> str:
    """Delete exactly one existing regular repository file."""

    target = _existing_regular_file(workspace_root, path)
    try:
        target.unlink()
    except OSError as error:
        raise RepositoryError(f"Unable to delete repository file: {path}") from error
    return f"Deleted {path}."


def move_file(
    workspace_root: Path,
    *,
    source_path: str,
    destination_path: str,
) -> str:
    """Move one existing regular file without overwriting the destination."""

    source = _existing_regular_file(workspace_root, source_path)
    destination = resolve_workspace_path(workspace_root, destination_path)
    if destination.exists():
        raise RepositoryError(
            f"Destination repository path already exists: {destination_path}"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination = resolve_workspace_path(workspace_root, destination_path)
    try:
        os.replace(source, destination)
    except OSError as error:
        raise RepositoryError(
            f"Unable to move {source_path} to {destination_path}."
        ) from error
    return f"Moved {source_path} to {destination_path}."


def _existing_regular_file(workspace_root: Path, path: str) -> Path:
    target = resolve_workspace_path(workspace_root, path)
    if not target.is_file():
        raise RepositoryError(f"Repository file does not exist: {path}")
    return target


def _read_utf8(path: Path, display_path: str) -> str:
    try:
        data = path.read_bytes()
    except OSError as error:
        raise RepositoryError(
            f"Unable to read repository file: {display_path}"
        ) from error
    if b"\x00" in data[:8192]:
        raise RepositoryError(f"Binary repository files cannot be edited: {display_path}")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise RepositoryError(
            f"Repository file is not valid UTF-8 text: {display_path}"
        ) from error


def _write_atomic(path: Path, content: str) -> None:
    try:
        encoded = content.encode("utf-8")
        target_mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            dir=path.parent,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(file_descriptor, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, target_mode)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    except OSError as error:
        raise RepositoryError(f"Unable to write repository file: {path.name}") from error


def _validate_text_size(value: str, *, name: str) -> None:
    if "\x00" in value:
        raise RepositoryError(f"{name} must be UTF-8 text without NUL bytes.")
    if len(value) > MAX_EDIT_CHARS:
        raise RepositoryError(
            f"{name} exceeds the {MAX_EDIT_CHARS}-character edit limit."
        )
