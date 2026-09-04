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


class MemoryRetrievalStatus(StrEnum):
    """Whether Issue-relevant memory can be exposed to a caller."""

    USED = "used"
    NO_MATCH = "no_match"
    UNAVAILABLE = "unavailable"
    DISABLED = "disabled"


class MemoryRetrievalOutcome(StrEnum):
    """Specific, observable reason for a retrieval status."""

    USEFUL_CONTEXT = "useful_context"
    USEFUL_CONTEXT_TRUNCATED = "useful_context_truncated"
    NO_LEXICAL_CANDIDATES = "no_lexical_candidates"
    BELOW_THRESHOLD = "below_threshold"
    GRAPH_UNAVAILABLE = "graph_unavailable"


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


class MemoryRetrievalBudgets(BaseModel):
    """Hard deterministic limits for one Issue retrieval."""

    model_config = ConfigDict(frozen=True)

    max_results: int = Field(default=12, ge=1, le=50)
    max_seeds: int = Field(default=5, ge=1, le=20)
    max_related_per_seed: int = Field(default=8, ge=1, le=50)
    max_chars: int = Field(default=12_000, ge=500, le=50_000)
    max_issue_chars: int = Field(default=50_000, ge=1_000, le=200_000)
    usefulness_threshold: float = Field(default=5.0, ge=0.0, le=100.0)


class MemoryRelationshipEvidence(BaseModel):
    """One graph fact explaining why a related item was selected."""

    model_config = ConfigDict(frozen=True)

    reason: str = Field(min_length=1, max_length=40)
    relationship: str = Field(min_length=1, max_length=40)
    seed_qualified_name: str = Field(min_length=1, max_length=1_000)


class MemoryRetrievalItem(BaseModel):
    """One ranked, source-locating retrieval result."""

    model_config = ConfigDict(frozen=True)

    rank: int = Field(ge=1)
    kind: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=300)
    qualified_name: str = Field(min_length=1, max_length=1_000)
    file_path: str = Field(min_length=1, max_length=500)
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    language: str = Field(min_length=1, max_length=40)
    is_test: bool = False
    signature: str = Field(default="", max_length=500)
    score: float = Field(ge=0.0)
    reasons: tuple[str, ...] = ()
    relationships: tuple[MemoryRelationshipEvidence, ...] = ()


class MemoryRetrievalResult(BaseModel):
    """Bounded, explainable result of retrieving memory for one Issue."""

    model_config = ConfigDict(frozen=True)

    status: MemoryRetrievalStatus
    outcome: MemoryRetrievalOutcome
    summary: str = Field(max_length=500)
    memory_file: Path
    repository_id: str | None = None
    indexed_sha: str | None = None
    last_updated: str | None = None
    search_modes: tuple[str, ...] = ()
    query_terms: tuple[str, ...] = ()
    lexical_candidates: int = Field(default=0, ge=0)
    expanded_candidates: int = Field(default=0, ge=0)
    total_candidates: int = Field(default=0, ge=0)
    returned: int = Field(default=0, ge=0)
    omitted: int = Field(default=0, ge=0)
    truncated: bool = False
    context: str = ""
    context_chars: int = Field(default=0, ge=0)
    items: tuple[MemoryRetrievalItem, ...] = ()
    warnings: tuple[str, ...] = ()
    duration_ms: float = Field(default=0.0, ge=0.0)


class MemoryToolCallRecord(BaseModel):
    """Bounded evidence for one native memory tool invocation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    call_number: int = Field(ge=1)
    tool_name: str = Field(min_length=1, max_length=100)
    status: str = Field(min_length=1, max_length=40)
    hit_count: int = Field(default=0, ge=0)
    returned_paths: tuple[str, ...] = Field(default=(), max_length=20)
    duration_ms: float = Field(ge=0.0)
    truncated: bool = False


class LegionMemoryRunArtifact(BaseModel):
    """Run evidence for optional memory preparation, retrieval, and usage."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    format_version: int = 1
    requested_memory_file: Path
    resolved_memory_file: Path
    status: MemoryRetrievalStatus
    repository_id: str | None = None
    indexed_sha: str | None = None
    build: MemoryBuildResult | None = None
    retrieval: MemoryRetrievalResult | None = None
    tool_calls: tuple[MemoryToolCallRecord, ...] = ()
    failure_category: str | None = Field(default=None, max_length=100)
    fallback: str = Field(max_length=200)
