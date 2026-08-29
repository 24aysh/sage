"""Active raw-source provenance and healthy-mode mutation policy."""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath

from sage.errors import MemoryPolicyError
from sage.memory.canonical import text_digest
from sage.memory.models import ContextEntry, MutationAuthorization, SourceReadEvent
from sage.repository.paths import resolve_workspace_path, workspace_relative_path


class ActiveContext:
    """Own source coverage and edit authorization independently of orchestration."""

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace
        self._entries: dict[str, ContextEntry] = {}
        self._coverage: dict[str, list[tuple[int, int, str]]] = {}
        self._authorized_directories: set[str] = {"."}

    @property
    def entries(self) -> tuple[ContextEntry, ...]:
        return tuple(self._entries.values())

    @property
    def file_count(self) -> int:
        return len(self._entries)

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(self._entries)

    @property
    def source_chars(self) -> int:
        return sum(len(item.source or "") for item in self._entries.values())

    def contains(self, path: str) -> bool:
        normalized = workspace_relative_path(self._workspace, path)
        return normalized in self._entries

    def add(self, entry: ContextEntry, *, complete_source_digest: str) -> None:
        self._entries[entry.path] = entry
        for start, end in entry.included_line_ranges:
            self._coverage.setdefault(entry.path, []).append(
                (start, end, complete_source_digest)
            )
        self.authorize_parent(entry.path)

    def record_source_read(
        self, path: str, *, start_line: int, end_line: int | None
    ) -> None:
        normalized = workspace_relative_path(self._workspace, path)
        source = _read_workspace_text(
            resolve_workspace_path(self._workspace, normalized)
        )
        lines = source.splitlines()
        actual_end = min(end_line or start_line + 299, max(1, len(lines)))
        self._coverage.setdefault(normalized, []).append(
            (start_line, actual_end, text_digest(source))
        )
        self.authorize_parent(normalized)

    def record_event(self, event: SourceReadEvent) -> None:
        self._coverage.setdefault(event.path, []).append(
            (
                event.start_line,
                event.end_line or event.start_line,
                text_digest(event.source),
            )
        )

    def authorize_parent(self, path: str) -> None:
        parent = str(PurePosixPath(path).parent) or "."
        self._authorized_directories.add(parent)

    def authorize_read(self, path: str) -> str:
        normalized = workspace_relative_path(self._workspace, path)
        if normalized not in self._entries:
            raise MemoryPolicyError(
                "Use expand_context or materialize_dependency before reading this path."
            )
        return normalized

    def authorize_search(self, path: str) -> str:
        normalized = workspace_relative_path(self._workspace, path)
        active = normalized in self._entries or any(
            entry.startswith(f"{normalized}/") for entry in self._entries
        )
        if normalized == "." or not active:
            raise MemoryPolicyError(
                "Healthy memory limits text search to an active file or directory."
            )
        return normalized

    def require_dependency_provenance(self, reason: str) -> None:
        if not any(path in reason for path in self._entries):
            raise MemoryPolicyError(
                "Dependency materialization reason must reference an active source path."
            )

    def describe(self) -> str:
        payload = [
            {
                "path": entry.path,
                "role": entry.role,
                "added_by": entry.added_by,
                "reason": entry.reason,
                "evidence_tier": entry.evidence_tier,
                "materialization": entry.materialization,
                "included_line_ranges": entry.included_line_ranges,
                "current_read_ranges": [
                    (start, end)
                    for start, end, _digest in self._coverage.get(entry.path, ())
                ],
            }
            for entry in self._entries.values()
        ]
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    def authorize_tree(self, path: str, *, max_depth: int) -> str:
        normalized = workspace_relative_path(self._workspace, path)
        if normalized not in self._authorized_directories and not any(
            entry == normalized or entry.startswith(f"{normalized}/")
            for entry in self._entries
        ):
            raise MemoryPolicyError(
                "Healthy memory permits tree expansion only around active context."
            )
        if max_depth > 2:
            raise MemoryPolicyError("Healthy memory limits tree expansion to depth two.")
        if normalized == "." and max_depth > 1:
            raise MemoryPolicyError(
                "Healthy memory requires bounded root listing at depth one."
            )
        self._authorized_directories.add(normalized)
        return normalized

    def authorize_mutation(self, request: MutationAuthorization) -> None:
        path = workspace_relative_path(self._workspace, request.path)
        resolved = resolve_workspace_path(self._workspace, path)
        if request.operation == "write" and not resolved.exists():
            self._require_parent_authorized(path)
            return
        source = _read_workspace_text(resolved)
        digest = text_digest(source)
        ranges = [
            item[:2] for item in self._coverage.get(path, ()) if item[2] == digest
        ]
        if not ranges:
            raise MemoryPolicyError(
                "Read the current file source before mutating it in healthy memory mode."
            )
        line_count = max(1, len(source.splitlines()))
        if request.operation == "replace" and request.old_text is not None:
            for start, end in _occurrence_ranges(source, request.old_text):
                if not _range_covered(start, end, ranges):
                    raise MemoryPolicyError(
                        "The replacement target is outside the current read coverage."
                    )
            return
        if not _range_covered(1, line_count, ranges):
            raise MemoryPolicyError(
                "Full file coverage is required for replace, delete, or move."
            )
        if request.destination_path:
            self._require_parent_authorized(request.destination_path)

    def record_mutation(self, paths: tuple[str, ...]) -> None:
        for path in paths:
            normalized = workspace_relative_path(self._workspace, path)
            self._coverage.pop(normalized, None)
            entry = self._entries.get(normalized)
            if entry is not None:
                self._entries[normalized] = entry.model_copy(
                    update={
                        "source": None,
                        "materialization": "metadata_only",
                        "included_line_ranges": (),
                    }
                )

    def _require_parent_authorized(self, path: str) -> None:
        normalized = workspace_relative_path(self._workspace, path)
        parent = str(PurePosixPath(normalized).parent) or "."
        if parent not in self._authorized_directories:
            raise MemoryPolicyError(
                "Materialize or expand the destination parent before creating or moving a file."
            )


def _read_workspace_text(path: Path) -> str:
    data = path.read_bytes()
    if b"\x00" in data[:8192]:
        raise MemoryPolicyError("Binary files cannot satisfy memory read coverage.")
    return data.decode("utf-8")


def _occurrence_ranges(source: str, target: str) -> list[tuple[int, int]]:
    if not target:
        raise MemoryPolicyError("Replacement authorization requires non-empty text.")
    result: list[tuple[int, int]] = []
    offset = 0
    while True:
        index = source.find(target, offset)
        if index < 0:
            break
        start = source.count("\n", 0, index) + 1
        result.append((start, start + target.count("\n")))
        offset = index + len(target)
    if not result:
        raise MemoryPolicyError("Replacement text is not present in the current file.")
    return result


def _range_covered(
    start: int, end: int, ranges: list[tuple[int, int]]
) -> bool:
    cursor = start
    for range_start, range_end in sorted(ranges):
        if range_end < cursor:
            continue
        if range_start > cursor:
            return False
        cursor = max(cursor, range_end + 1)
        if cursor > end:
            return True
    return cursor > end
