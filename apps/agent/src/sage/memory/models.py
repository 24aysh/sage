"""Strict domain models for the sparse SMRT memory engine."""

from __future__ import annotations

import json
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
GitOid = Annotated[str, Field(pattern=r"^[0-9a-f]{40,64}$")]
ShortText = Annotated[str, Field(min_length=1, max_length=500)]


class MemoryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MemoryMode(StrEnum):
    DISABLED = "disabled"
    HEALTHY = "healthy"
    FALLBACK = "fallback"


class NodeType(StrEnum):
    FILE = "file"
    DIRECTORY = "directory"


class SemanticState(StrEnum):
    VALID = "valid"
    STALE = "stale"
    MISSING = "missing"


class CoverageState(StrEnum):
    PARTIAL = "partial"
    COMPLETE = "complete"


class SnapshotStatus(StrEnum):
    BUILDING = "BUILDING"
    READY = "READY"
    FAILED = "FAILED"


class RepositoryIdentity(MemoryModel):
    namespace_kind: Literal["github", "local"]
    namespace_key: str = Field(min_length=1, max_length=200)
    display_name: str = Field(min_length=1, max_length=300)


class RepositoryRecord(MemoryModel):
    repository_id: UUID
    identity: RepositoryIdentity
    latest_ready_snapshot_id: UUID | None = None


def _clean_items(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = " ".join(value.split())
        if cleaned and cleaned not in seen:
            result.append(cleaned)
            seen.add(cleaned)
    return result


class FileSemanticPayload(MemoryModel):
    summary: str = Field(min_length=1, max_length=2_000)
    responsibilities: list[ShortText] = Field(default_factory=list, max_length=20)
    concepts: list[ShortText] = Field(default_factory=list, max_length=30)

    _normalize = field_validator(
        "responsibilities", "concepts", mode="before"
    )(_clean_items)

    @field_validator("summary", mode="before")
    @classmethod
    def normalize_summary(cls, value: str) -> str:
        return " ".join(value.split())


class DirectorySemanticPayload(MemoryModel):
    summary: str = Field(min_length=1, max_length=2_000)
    responsibilities: list[ShortText] = Field(default_factory=list, max_length=20)
    not_responsible_for: list[ShortText] = Field(default_factory=list, max_length=20)
    concepts: list[ShortText] = Field(default_factory=list, max_length=30)

    _normalize = field_validator(
        "responsibilities", "not_responsible_for", "concepts", mode="before"
    )(_clean_items)

    @field_validator("summary", mode="before")
    @classmethod
    def normalize_summary(cls, value: str) -> str:
        return " ".join(value.split())


class FileStructure(MemoryModel):
    language: Literal["python", "typescript", "javascript", "unknown"]
    symbols: list[ShortText] = Field(default_factory=list, max_length=100)
    imports: list[ShortText] = Field(default_factory=list, max_length=100)
    exports: list[ShortText] = Field(default_factory=list, max_length=100)
    signatures: list[ShortText] = Field(default_factory=list, max_length=100)
    parser_version: str = Field(min_length=1, max_length=100)
    parse_status: Literal["parsed", "partial", "unsupported", "skipped"]

    _normalize = field_validator(
        "symbols", "imports", "exports", "signatures", mode="before"
    )(_clean_items)


class SemanticObject(MemoryModel):
    semantic_digest: Digest
    payload_digest: Digest
    node_type: NodeType
    source_oid: GitOid
    semantic_payload: FileSemanticPayload | DirectorySemanticPayload
    structure: FileStructure | None = None
    derived_from: tuple[tuple[str, Digest], ...] = ()
    schema_version: str = "smrt-semantic-v1"
    summarizer_provider: str = Field(min_length=1, max_length=80)
    summarizer_model: str = Field(min_length=1, max_length=120)
    prompt_version: str = Field(min_length=1, max_length=80)
    parser_version: str | None = Field(default=None, max_length=100)
    generation_mode: Literal["full", "delta"] = "full"
    delta_depth: int = Field(default=0, ge=0, le=8)

    @model_validator(mode="after")
    def validate_node_payload(self) -> "SemanticObject":
        if self.node_type is NodeType.FILE:
            if not isinstance(self.semantic_payload, FileSemanticPayload):
                raise ValueError("A file requires a file semantic payload.")
            if self.structure is None:
                raise ValueError("A file requires deterministic structure metadata.")
        elif not isinstance(self.semantic_payload, DirectorySemanticPayload):
            raise ValueError("A directory requires a directory semantic payload.")
        elif self.structure is not None:
            raise ValueError("A directory cannot carry file structure.")
        return self


class OverlayNode(MemoryModel):
    overlay_digest: Digest
    node_type: NodeType
    source_oid: GitOid
    semantic_digest: Digest | None = None
    stale_hint_digest: Digest | None = None
    semantic_state: SemanticState = SemanticState.MISSING
    coverage_state: CoverageState | None = None
    children: tuple[tuple[str, Digest], ...] = ()

    @model_validator(mode="after")
    def validate_state(self) -> "OverlayNode":
        if self.semantic_state is SemanticState.VALID and self.semantic_digest is None:
            raise ValueError("Valid overlay nodes require a semantic object.")
        if self.semantic_state is SemanticState.MISSING and self.semantic_digest is not None:
            raise ValueError("Missing overlay nodes cannot claim valid semantics.")
        if self.node_type is NodeType.FILE and self.coverage_state is not None:
            raise ValueError("Files cannot carry directory coverage.")
        for name, _ in self.children:
            if name in {"", ".", ".."} or any(
                character in name for character in ("/", "\\", "\x00")
            ):
                raise ValueError("Overlay children require one safe Git path segment.")
        return self


class Snapshot(MemoryModel):
    snapshot_id: UUID
    repository_id: UUID
    parent_snapshot_id: UUID | None = None
    target_commit_oid: GitOid
    target_root_tree_oid: GitOid
    root_overlay_digest: Digest | None = None
    status: SnapshotStatus
    run_id: str = Field(min_length=1, max_length=200)
    created_at: datetime
    ready_at: datetime | None = None
    schema_version: str = "smrt-overlay-v1"
    failure_code: str | None = Field(default=None, max_length=100)


class ContextEntry(MemoryModel):
    path: str = Field(min_length=1, max_length=4_096)
    base_blob_oid: GitOid | None = None
    workspace_content_digest: Digest
    role: Literal["primary", "supporting", "verification"] = "supporting"
    added_by: Literal[
        "initial_smrt_forest", "deterministic_dependency", "smrt_expansion"
    ]
    reason: str = Field(min_length=1, max_length=500)
    evidence_tier: str = Field(min_length=1, max_length=80)
    materialization: Literal["full", "excerpt", "metadata_only"]
    included_line_ranges: tuple[tuple[int, int], ...] = ()
    source: str | None = Field(default=None, max_length=120_000)


class ContextForest(MemoryModel):
    entries: tuple[ContextEntry, ...] = ()
    navigation_rounds: int = Field(default=0, ge=0)
    candidate_count: int = Field(default=0, ge=0)

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(item.path for item in self.entries)

    def render_for_solver(self) -> str:
        if not self.entries:
            return ""
        payload = [
            {
                "path": entry.path,
                "reason": entry.reason,
                "evidence_tier": entry.evidence_tier,
                "source": entry.source or "[metadata only]",
            }
            for entry in self.entries
        ]
        return (
            "<untrusted-memory-context-forest>\n"
            + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            + "\n</untrusted-memory-context-forest>"
        )


class ContextExpansionRequest(MemoryModel):
    query: str = Field(min_length=1, max_length=2_000)
    reason: str = Field(min_length=1, max_length=500)


class DirectMaterializationRequest(MemoryModel):
    path: str = Field(min_length=1, max_length=4_096)
    reason: str = Field(min_length=1, max_length=500)


class MutationAuthorization(MemoryModel):
    operation: Literal["replace", "write", "delete", "move"]
    path: str = Field(min_length=1, max_length=4_096)
    destination_path: str | None = Field(default=None, max_length=4_096)
    old_text: str | None = Field(default=None, max_length=120_000)
    replacing_entire_file: bool = False


class MemoryFailure(MemoryModel):
    component: str = Field(min_length=1, max_length=80)
    stage: str = Field(min_length=1, max_length=80)
    error_code: str = Field(min_length=1, max_length=100)
    safe_message: str = Field(min_length=1, max_length=500)
    snapshot_id: UUID | None = None
    target_commit: GitOid
    fallback_action: Literal["full repository exploration"] = (
        "full repository exploration"
    )


class MemoryRunReport(MemoryModel):
    mode: MemoryMode
    repository_identity_digest: Digest | None = None
    repository_display_name: str | None = Field(default=None, max_length=300)
    target_commit: GitOid | None = None
    input_snapshot_id: UUID | None = None
    output_snapshot_id: UUID | None = None
    reused_cards: int = Field(default=0, ge=0)
    created_cards: int = Field(default=0, ge=0)
    refreshed_cards: int = Field(default=0, ge=0)
    stale_cards: int = Field(default=0, ge=0)
    skipped_cards: int = Field(default=0, ge=0)
    fts_candidate_count: int = Field(default=0, ge=0)
    navigation_rounds: int = Field(default=0, ge=0)
    final_file_count: int = Field(default=0, ge=0)
    expansion_count: int = Field(default=0, ge=0)
    materialization_count: int = Field(default=0, ge=0)
    summarizer_calls: int = Field(default=0, ge=0)
    summarizer_input_tokens: int = Field(default=0, ge=0)
    summarizer_output_tokens: int = Field(default=0, ge=0)
    summarizer_latency_ms: float = Field(default=0, ge=0)
    snapshot_published: bool = False
    retained_snapshot_count: int = Field(default=0, ge=0, le=5)
    failure: MemoryFailure | None = None


class MemoryRunRequest(MemoryModel):
    identity: RepositoryIdentity
    run_id: str = Field(min_length=1, max_length=200)
    target_commit: GitOid
    workspace_path: Path


class SearchDocument(MemoryModel):
    path: str = Field(min_length=1, max_length=4_096)
    node_type: NodeType
    source_oid: GitOid | None = None
    semantic_digest: Digest | None = None
    stale_hint_digest: Digest | None = None
    payload_digest: Digest | None = None
    summary: str = Field(default="", max_length=2_000)
    responsibilities: tuple[str, ...] = ()
    not_responsible_for: tuple[str, ...] = ()
    concepts: tuple[str, ...] = ()
    symbols: tuple[str, ...] = ()
    imports: tuple[str, ...] = ()
    derived_from: tuple[tuple[str, Digest], ...] = ()
    generation_mode: Literal["full", "delta"] = "full"
    delta_depth: int = Field(default=0, ge=0, le=8)
    semantic_state: SemanticState = SemanticState.MISSING


class RetrievalCandidate(MemoryModel):
    path: str = Field(min_length=1, max_length=4_096)
    node_type: NodeType
    score: float
    evidence_tier: str = Field(min_length=1, max_length=80)
    ancestry: str = Field(min_length=1, max_length=4_096)
    reason: str = Field(min_length=1, max_length=500)


class SourceReadEvent(MemoryModel):
    path: str = Field(min_length=1, max_length=4_096)
    start_line: int = Field(default=1, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    source: str = Field(max_length=120_000)
