"""Run-scoped Legion Memory binding and bounded native-tool usage evidence."""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from time import perf_counter

from sage.domain.memory import (
    LegionMemoryRunArtifact,
    MemoryBuildResult,
    MemoryRetrievalResult,
    MemoryRetrievalStatus,
    MemoryToolCallRecord,
)
from sage.legion_memory.service import LegionMemoryService
from sage.legion_memory.context import render_structural_context
from sage.errors import LegionMemoryQueryError

logger = logging.getLogger(__name__)


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
    _enriched: set[str] = field(default_factory=set)
    _enrichment_records: list[MemoryToolCallRecord] = field(default_factory=list)
    _enrichment_chars: int = 0

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
            enrichments=tuple(self._enrichment_records),
            embedding_usage=(self.service.vectors.provider.usage.model_copy() if self.service.vectors else None),
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

    def enrich(
        self, *, tool_name: str, available_chars: int, path: str | None = None,
        query: str | None = None, start_line: int = 1, end_line: int | None = None,
    ) -> str:
        """Append deduplicated accepted-base evidence without replacing source."""
        if self._closed:
            return ""
        started = perf_counter()
        limit = min(available_chars, 3000, 16_000 - self._enrichment_chars)
        output = "\n\n<legion-read-context>\nAccepted-base graph context; verify against current source.\n"
        suffix = "\n</legion-read-context>"
        selected = []
        status = "skipped"
        truncated = limit < 200
        try:
            rows = self.service.enrich_repository_read(
                repo_root=self.repo_root, memory_file=self.memory_file,
                path=path, query=query, start_line=start_line, end_line=end_line,
            ) if limit >= 200 else []
            for row in rows:
                block = render_structural_context(row)
                identity = hashlib.sha256(block.encode()).hexdigest()
                if identity in self._enriched:
                    continue
                if len(output) + len(block) + len(suffix) + 1 > limit:
                    truncated = True
                    continue
                output += block + "\n"
                self._enriched.add(identity)
                selected.append(row)
            status = "used" if selected else "skipped"
        except (LegionMemoryQueryError, sqlite3.Error, OSError, ValueError):
            status = "unavailable"
        rendered = output + suffix if selected else ""
        self._enrichment_chars += len(rendered)
        self._enrichment_records.append(MemoryToolCallRecord(
            call_number=len(self._enrichment_records) + 1, tool_name=tool_name,
            status=status, hit_count=len(selected), returned_paths=_returned_paths(selected),
            duration_ms=round((perf_counter() - started) * 1000, 2),
            truncated=truncated,
        ))
        logger.info("Legion Memory enrichment: %s; tool=%s; symbols=%d", status, tool_name, len(selected))
        return rendered

    def close(self) -> None:
        """Prevent late usage recording after workflow cleanup."""

        self._closed = True
        self._enriched.clear()
        if self.service.vectors is not None:
            self.service.vectors.clear_query_cache()


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
        embedding_usage=(
            retrieval.vectors.usage if retrieval and retrieval.vectors.usage
            else build.vectors.usage if build else None
        ),
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
