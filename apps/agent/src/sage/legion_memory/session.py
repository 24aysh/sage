"""Run-scoped Legion Memory binding and bounded native-tool usage evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from sage.domain.memory import (
    LegionMemoryRunArtifact,
    MemoryBuildResult,
    MemoryRetrievalResult,
    MemoryRetrievalStatus,
    MemoryToolCallRecord,
)
from sage.legion_memory.service import LegionMemoryService


@dataclass
class MemorySession:
    """Validated base-snapshot memory available during one Solver run."""

    service: LegionMemoryService
    repo_root: Path
    requested_memory_file: Path
    memory_file: Path
    build: MemoryBuildResult
    retrieval: MemoryRetrievalResult
    _tool_calls: list[MemoryToolCallRecord] = field(default_factory=list)
    _closed: bool = False

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def initial_context(self) -> str | None:
        if self.retrieval.status is MemoryRetrievalStatus.USED:
            return self.retrieval.context
        return None

    @property
    def tool_calls(self) -> tuple[MemoryToolCallRecord, ...]:
        return tuple(self._tool_calls)

    def record_tool_call(
        self,
        tool_name: str,
        result: dict[str, object],
        duration_ms: float,
    ) -> None:
        """Record result metadata without retaining arguments or graph payloads."""

        if self._closed:
            return
        self._tool_calls.append(
            MemoryToolCallRecord(
                call_number=len(self._tool_calls) + 1,
                tool_name=tool_name,
                status=str(result.get("status") or "unknown")[:40],
                hit_count=max(0, _integer(result.get("returned"))),
                returned_paths=_returned_paths(result.get("data")),
                duration_ms=max(0.0, round(duration_ms, 2)),
                truncated=bool(result.get("truncated", False)),
            )
        )

    def artifact(self) -> LegionMemoryRunArtifact:
        """Snapshot current memory evidence for atomic artifact persistence."""

        return LegionMemoryRunArtifact(
            requested_memory_file=self.requested_memory_file,
            resolved_memory_file=self.memory_file,
            status=self.retrieval.status,
            repository_id=self.build.repository_id,
            indexed_sha=self.build.indexed_sha,
            build=self.build,
            retrieval=self.retrieval,
            tool_calls=self.tool_calls,
            fallback=(
                "not needed"
                if self.retrieval.status is MemoryRetrievalStatus.USED
                else "normal repository inspection"
            ),
        )

    def close(self) -> None:
        """Prevent late usage recording after workflow cleanup."""

        self._closed = True


def unavailable_memory_artifact(
    *,
    requested_memory_file: Path,
    resolved_memory_file: Path,
    failure_category: str,
    build: MemoryBuildResult | None = None,
    retrieval: MemoryRetrievalResult | None = None,
) -> LegionMemoryRunArtifact:
    """Build secret-safe fallback evidence without raw exception content."""

    return LegionMemoryRunArtifact(
        requested_memory_file=requested_memory_file,
        resolved_memory_file=resolved_memory_file,
        status=MemoryRetrievalStatus.UNAVAILABLE,
        repository_id=build.repository_id if build else None,
        indexed_sha=build.indexed_sha if build else None,
        build=build,
        retrieval=retrieval,
        failure_category=failure_category[:100],
        fallback="normal repository inspection",
    )


def _returned_paths(value: object) -> tuple[str, ...]:
    found: list[str] = []

    def visit(item: object, *, key: str = "", remaining: int = 500) -> int:
        if remaining <= 0 or len(found) >= 20:
            return remaining
        remaining -= 1
        if isinstance(item, Mapping):
            for nested_key, nested in item.items():
                remaining = visit(nested, key=str(nested_key), remaining=remaining)
                if remaining <= 0 or len(found) >= 20:
                    break
            return remaining
        if isinstance(item, Sequence) and not isinstance(item, (str, bytes)):
            for nested in item:
                remaining = visit(nested, key=key, remaining=remaining)
                if remaining <= 0 or len(found) >= 20:
                    break
            return remaining
        if key in {"file_path", "path"} or key.endswith("_files"):
            path = _safe_relative_path(str(item))
            if path and path not in found:
                found.append(path)
        return remaining

    visit(value)
    return tuple(found)


def _safe_relative_path(value: str) -> str | None:
    if not value or len(value) > 500:
        return None
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        return None
    return path.as_posix()


def _integer(value: object) -> int:
    try:
        return int(value) if value is not None else 0
    except (TypeError, ValueError):
        return 0
