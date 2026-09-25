"""Provider-neutral contracts for repository indexing and retrieval."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field
from sage.domain.relevance import RelevanceReport


class IndexBuildType(StrEnum):
    """How the graph reached its current ready state."""

    FULL = "full"
    INCREMENTAL = "incremental"
    NO_CHANGE = "no_change"


class IndexStatus(StrEnum):
    """Availability of one graph operation."""

    READY = "ready"
    MISSING = "missing"
    UNAVAILABLE = "unavailable"


class RetrievalStatus(StrEnum):
    """Whether Issue-relevant context can be exposed to a caller."""

    USED = "used"
    NO_MATCH = "no_match"
    UNAVAILABLE = "unavailable"
    DISABLED = "disabled"


class RetrievalOutcome(StrEnum):
    """Specific, observable reason for a retrieval status."""

    USEFUL_CONTEXT = "useful_context"
    USEFUL_CONTEXT_TRUNCATED = "useful_context_truncated"
    NO_LEXICAL_CANDIDATES = "no_lexical_candidates"
    BELOW_THRESHOLD = "below_threshold"
    GRAPH_UNAVAILABLE = "graph_unavailable"
    RELEVANCE_REJECTED = "relevance_rejected"
    RELEVANCE_UNAVAILABLE = "relevance_unavailable"
    CONTEXT_BUDGET_EXHAUSTED = "context_budget_exhausted"


class RepositoryGraphStats(BaseModel):
    """Bounded health and size summary for one graph database."""

    model_config = ConfigDict(frozen=True)

    status: IndexStatus
    index_file: Path
    repository_id: str | None = None
    indexed_sha: str | None = None
    schema_version: int | None = None
    build_type: IndexBuildType | None = None
    files: int = Field(default=0, ge=0)
    nodes: int = Field(default=0, ge=0)
    edges: int = Field(default=0, ge=0)
    flows: int = Field(default=0, ge=0)
    communities: int = Field(default=0, ge=0)
    languages: tuple[str, ...] = ()
    last_updated: str | None = None


class IndexBuildResult(BaseModel):
    """Outcome of one repository-index build or update."""

    model_config = ConfigDict(frozen=True)

    status: IndexStatus = IndexStatus.READY
    build_type: IndexBuildType
    index_file: Path
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


class GraphToolResult(BaseModel):
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


class RetrievalBudgets(BaseModel):
    """Hard deterministic limits for one Issue retrieval."""

    model_config = ConfigDict(frozen=True)

    max_results: int = Field(default=12, ge=1, le=50)
    max_seeds: int = Field(default=5, ge=1, le=20)
    max_related_per_seed: int = Field(default=8, ge=1, le=50)
    max_chars: int = Field(default=12_000, ge=500, le=50_000)
    max_issue_chars: int = Field(default=50_000, ge=1_000, le=200_000)
    usefulness_threshold: float = Field(default=5.0, ge=0.0, le=100.0)


class RetrievalRelationshipEvidence(BaseModel):
    """One graph fact explaining why a related item was selected."""

    model_config = ConfigDict(frozen=True)

    reason: str = Field(min_length=1, max_length=40)
    relationship: str = Field(min_length=1, max_length=40)
    seed_qualified_name: str = Field(min_length=1, max_length=1_000)


class RetrievalItem(BaseModel):
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
    relationships: tuple[RetrievalRelationshipEvidence, ...] = ()


class RetrievalCandidateDiagnostic(BaseModel):
    """Bounded ranking evidence, not model context or a quality score."""

    qualified_name: str
    channel_ranks: dict[str, int] = Field(default_factory=dict)
    reasons: tuple[str, ...] = ()
    score: float = 0
    selection: str = "not_selected"


class RetrievalResult(BaseModel):
    """Bounded, explainable result of retrieving context for one Issue."""

    model_config = ConfigDict(frozen=True)

    status: RetrievalStatus
    outcome: RetrievalOutcome
    summary: str = Field(max_length=500)
    index_file: Path
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
    items: tuple[RetrievalItem, ...] = ()
    warnings: tuple[str, ...] = ()
    duration_ms: float = Field(default=0.0, ge=0.0)
    ranking_duration_ms: float = Field(default=0.0, ge=0.0)
    diagnostics: tuple[RetrievalCandidateDiagnostic, ...] = Field(default=(), max_length=200)
    unresolved_edges: int = Field(default=0, ge=0)
    relevance_filter: RelevanceReport | None = None


class RetrievalToolCallRecord(BaseModel):
    """Bounded evidence for one native graph-tool invocation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    call_number: int = Field(ge=1)
    tool_name: str = Field(min_length=1, max_length=100)
    status: str = Field(min_length=1, max_length=40)
    hit_count: int = Field(default=0, ge=0)
    returned_paths: tuple[str, ...] = Field(default=(), max_length=20)
    duration_ms: float = Field(ge=0.0)
    truncated: bool = False
    context_chars: int = Field(default=0, ge=0)
    session_number: int = Field(default=0, ge=0)


class RetrievalExposure(BaseModel):
    """Observed exposure only; does not assert causal use or savings."""

    available: bool = False
    retrieved: bool = False
    exposed: bool = False
    queried: bool = False
    read_enriched: bool = False
    sessions: int = 0
    initial_context_chars: int = 0
    enrichment_chars: int = 0
    graph_response_chars: int = 0
    source_read_chars: int = 0
    tool_schema_chars: int = 0
    retrieved_paths_read: tuple[str, ...] = ()
    preflight_duration_ms: float = 0


class RetrievalRunArtifact(BaseModel):
    """Run evidence for optional index preparation, retrieval, and usage."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    format_version: int = 2
    exposure: RetrievalExposure = Field(default_factory=RetrievalExposure)
    enrichments: tuple[RetrievalToolCallRecord, ...] = ()
    requested_index_file: Path
    resolved_index_file: Path
    status: RetrievalStatus
    repository_id: str | None = None
    indexed_sha: str | None = None
    build: IndexBuildResult | None = None
    retrieval: RetrievalResult | None = None
    tool_calls: tuple[RetrievalToolCallRecord, ...] = ()
    failure_category: str | None = Field(default=None, max_length=100)
    fallback: str = Field(max_length=200)
