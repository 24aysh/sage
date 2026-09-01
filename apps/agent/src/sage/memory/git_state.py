"""Git-backed source authority for memory catch-up and materialization."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path, PurePosixPath

from sage.errors import MemoryStorageError
from sage.repository.host_git import run_git


class GitStateReader:
    """Read committed source objects without consulting candidate changes."""

    def __init__(self, repository: Path) -> None:
        self._repository = repository

    def root_tree_oid(self, commit_oid: str) -> str:
        result = run_git(
            ["rev-parse", "--verify", "--end-of-options", f"{commit_oid}^{{tree}}"],
            repository=self._repository,
        )
        if result.returncode != 0:
            raise MemoryStorageError("Unable to resolve the target Git tree.")
        return result.stdout.strip()

    def object_oid(self, commit_oid: str, path: str) -> str:
        revision = f"{commit_oid}^{{tree}}" if path in {"", "."} else f"{commit_oid}:{path}"
        result = run_git(
            ["rev-parse", "--verify", "--end-of-options", revision],
            repository=self._repository,
        )
        if result.returncode != 0:
            raise MemoryStorageError("Unable to resolve a target Git object.")
        return result.stdout.strip()

    def list_files(self, commit_oid: str) -> Sequence[tuple[str, str]]:
        result = run_git(
            ["ls-tree", "-r", "-z", "--full-tree", commit_oid],
            repository=self._repository,
        )
        if result.returncode != 0:
            raise MemoryStorageError("Unable to enumerate target Git objects.")
        entries: list[tuple[str, str]] = []
        for raw in result.stdout.split("\0"):
            if not raw:
                continue
            try:
                metadata, path = raw.split("\t", 1)
                mode, object_type, oid = metadata.split(" ", 2)
            except ValueError as error:
                raise MemoryStorageError("Git returned an invalid tree record.") from error
            if object_type == "blob" and mode not in {"120000", "160000"}:
                safe_path = PurePosixPath(path)
                if safe_path.is_absolute() or ".." in safe_path.parts:
                    raise MemoryStorageError("Git returned an unsafe tree path.")
                entries.append((path, oid))
        return entries

    def read_blob(self, commit_oid: str, path: str) -> tuple[str, str]:
        safe_path = PurePosixPath(path)
        if safe_path.is_absolute() or ".." in safe_path.parts:
            raise MemoryStorageError("Memory source path is unsafe.")
        oid_result = run_git(
            ["rev-parse", "--verify", "--end-of-options", f"{commit_oid}:{path}"],
            repository=self._repository,
        )
        if oid_result.returncode != 0:
            raise MemoryStorageError("Unable to resolve a committed source file.")
        source_result = run_git(
            ["show", f"{commit_oid}:{path}"],
            repository=self._repository,
        )
        if source_result.returncode != 0:
            raise MemoryStorageError("Unable to read a committed source file.")
        if "\x00" in source_result.stdout:
            raise MemoryStorageError("Binary files cannot be materialized as text.")
        return oid_result.stdout.strip(), source_result.stdout
