"""Typed, evaluation-only records for retrieval noise experiments."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


IssueStatus = Literal[
    "pending",
    "judged",
    "all_rejected",
    "no_candidates",
    "filter_unavailable",
    "retrieval_unavailable",
    "interrupted",
    "not_run",
]


class DatasetIssue(BaseModel):
    """One validated Issue and its labels; the Issue body is never serialized."""

    model_config = ConfigDict(frozen=True)

    number: int = Field(ge=1)
    issue_id: str
    path: Path
    text: str = Field(exclude=True)
    sha256: str
    gold_files: tuple[str, ...]
    duplicate_gold_paths: int = Field(default=0, ge=0)


class Dataset(BaseModel):
    model_config = ConfigDict(frozen=True)

    issues_dir: Path
    correct_file: Path
    correct_sha256: str
    issues: tuple[DatasetIssue, ...]


class IssueMetrics(BaseModel):
    """Set metrics for one Issue; undefined percentages remain ``None``."""

    model_config = ConfigDict(frozen=True)

    gold_count: int = 0
    raw_count: int = 0
    raw_correct_count: int = 0
    accepted_count: int | None = None
    accepted_correct_count: int | None = None
    final_count: int | None = None
    final_correct_count: int | None = None
    noise_before_pct: float | None = None
    noise_after_jev_pct: float | None = None
    noise_after_final_pct: float | None = None
    noise_reduction_pp: float | None = None
    noise_files_removed: int | None = None
    noise_removed_pct: float | None = None
    retain_pct: float | None = None
    retrieved_correct_survival_pct: float | None = None
    raw_correct_recall_pct: float | None = None
    post_jev_correct_recall_pct: float | None = None
    final_context_correct_recall_pct: float | None = None
    retrieval_misses: tuple[str, ...] = ()
    correct_files_dropped: tuple[str, ...] = ()
    irrelevant_files_removed: tuple[str, ...] = ()
    context_omitted_files: tuple[str, ...] = ()


class IssueEvaluation(BaseModel):
    """Auditable evidence for one selected Issue."""

    model_config = ConfigDict(frozen=True)

    number: int = Field(ge=1)
    issue_id: str
    issue_file: str
    issue_sha256: str
    status: IssueStatus
    reason: str | None = None
    gold_files: tuple[str, ...]
    duplicate_gold_paths: int = 0
    absent_gold_files: tuple[str, ...] = ()
    unsupported_gold_files: tuple[str, ...] = ()
    raw_files: tuple[str, ...] = ()
    accepted_files: tuple[str, ...] = ()
    final_files: tuple[str, ...] = ()
    rejected_files: tuple[str, ...] = ()
    withheld_files: tuple[str, ...] = ()
    raw_items: tuple[dict[str, object], ...] = ()
    scores: dict[str, float] = Field(default_factory=dict)
    confidences: dict[str, float] = Field(default_factory=dict)
    filter_policy: str | None = None
    retrieval_status: str | None = None
    retrieval_outcome: str | None = None
    retrieval_duration_ms: float | None = None
    jev_duration_ms: float | None = None
    jev_input_tokens: int | None = None
    jev_output_tokens: int | None = None
    jev_attempts: int = 0
    metrics: IssueMetrics | None = None


class AggregateMetric(BaseModel):
    model_config = ConfigDict(frozen=True)

    mean: float | None
    eligible: int = Field(ge=0)
    excluded: int = Field(ge=0)
    exclusion_reasons: dict[str, int] = Field(default_factory=dict)


class EvaluationSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    counts: dict[str, int]
    average_noise_before_pct: AggregateMetric
    average_noise_after_jev_pct: AggregateMetric
    average_noise_reduction_pp: AggregateMetric
    average_retain_pct: AggregateMetric
    average_retrieved_correct_survival_pct: AggregateMetric
    average_raw_correct_recall_pct: AggregateMetric
    average_post_jev_correct_recall_pct: AggregateMetric
    total_irrelevant_files_removed: int = 0


class RunProvenance(BaseModel):
    model_config = ConfigDict(frozen=True)

    repository: str
    repository_id: str
    repository_sha: str
    repository_dirty: bool
    graph_source: str
    graph_source_sha256: str
    graph_snapshot: str
    graph_snapshot_sha256: str
    schema_version: int
    parser_version: str
    index_last_updated: str | None = None
    sage_sha: str | None = None
    sage_dirty: bool | None = None
    issues_dir: str
    correct_sha256: str


class EffectiveSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    jev_mode: Literal["on"] = "on"
    jev_model: str
    score_threshold: float
    confidence_threshold: float
    timeout_seconds: float
    log_input: bool
    capture: bool
    retrieval_max_results: int
    retrieval_staging_chars: int
    final_context_chars: int


class EvaluationResults(BaseModel):
    model_config = ConfigDict(frozen=True)

    format_version: int = 1
    run_id: str
    status: Literal["running", "complete", "partial"]
    started_at: datetime
    completed_at: datetime | None = None
    output_dir: str
    provenance: RunProvenance
    settings: EffectiveSettings
    summary: EvaluationSummary
    issues: tuple[IssueEvaluation, ...]
