"""Provider-neutral contracts for Legion Memory graph operations."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class MemoryBuildType(StrEnum):
    """How the graph reached its current ready state."""

    FULL = "full"
    INCREMENTAL = "incremental"
    NO_CHANGE = "no_change"


class MemoryStatus(StrEnum):
    """Availability of one graph operation."""

    READY = "ready"
    MISSING = "missing"
    UNAVAILABLE = "unavailable"


class MemoryGraphStats(BaseModel):
    """Bounded health and size summary for one graph database."""

    model_config = ConfigDict(frozen=True)

    status: MemoryStatus
    memory_file: Path
    repository_id: str | None = None
    indexed_sha: str | None = None
    schema_version: int | None = None
    build_type: MemoryBuildType | None = None
    files: int = Field(default=0, ge=0)
    nodes: int = Field(default=0, ge=0)
    edges: int = Field(default=0, ge=0)
    flows: int = Field(default=0, ge=0)
    communities: int = Field(default=0, ge=0)
    languages: tuple[str, ...] = ()
    last_updated: str | None = None


class MemoryBuildResult(BaseModel):
    """Outcome of the single Legion Memory build-or-update operation."""

    model_config = ConfigDict(frozen=True)

    status: MemoryStatus = MemoryStatus.READY
    build_type: MemoryBuildType
    memory_file: Path
    repository_id: str
    indexed_sha: str
    schema_version: int = Field(ge=1)
    files_indexed: int = Field(ge=0)
    files_parsed: int = Field(ge=0)
    files_removed: int = Field(ge=0)
    total_nodes: int = Field(ge=0)
    total_edges: int = Field(ge=0)
    total_flows: int = Field(ge=0)
    total_communities: int = Field(ge=0)
    languages: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    duration_ms: float = Field(ge=0)


class MemoryToolResult(BaseModel):
    """JSON-safe result shared by native read-only graph tools."""

    model_config = ConfigDict(frozen=True)

    status: str
    summary: str
    repository_id: str | None = None
    indexed_sha: str | None = None
    last_updated: str | None = None
    search_mode: str | None = None
    total: int = Field(default=0, ge=0)
    returned: int = Field(default=0, ge=0)
    omitted: int = Field(default=0, ge=0)
    truncated: bool = False
    data: dict[str, object] = Field(default_factory=dict)
