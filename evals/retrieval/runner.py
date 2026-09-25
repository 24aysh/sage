"""Graph snapshotting and production retrieval/Jev evaluation execution."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from sage.artifacts.files import write_json_atomic
from sage.composition import build_relevance_filter, build_retrieval_service
from sage.config import JevSettings
from sage.domain.retrieval import RetrievalBudgets, RetrievalStatus
from sage.errors import ConfigurationError
from sage.harness.jev.filter import RelevanceFilter, file_candidates
from sage.harness.retrieval.parsing import PARSER_VERSION, detect_language
from sage.harness.retrieval.store import GraphStore, SCHEMA_VERSION

from evals.retrieval.dataset import DatasetError, load_dataset, normalize_repo_path
from evals.retrieval.metrics import calculate_metrics, summarize
from evals.retrieval.models import (
    Dataset,
    DatasetIssue,
    EffectiveSettings,
    EvaluationResults,
    IssueEvaluation,
    RunProvenance,
)
from evals.retrieval.report import write_issue, write_results

logger = logging.getLogger(__name__)

STAGING_CHARS = 50_000
FINAL_CONTEXT_CHARS = 12_000
_FATAL_FILTER_REASONS = {"http_401", "http_403"}


class EvaluationPreflightError(ValueError):
    """Inputs or shared evaluation prerequisites are invalid."""


class IssueEvaluationInterrupted(BaseException):
    """Carry the current raw observation through task cancellation."""

    def __init__(
        self,
        record: IssueEvaluation,
        capture: dict | None,
        cause: BaseException,
    ) -> None:
        super().__init__("Issue evaluation interrupted")
        self.record = record
        self.capture = capture
        self.cause = cause


class Progress(Protocol):
    def update(self, amount: int = 1) -> object: ...
    def set_postfix_str(self, value: str) -> object: ...


@dataclass(frozen=True)
class PreparedEvaluation:
    run_id: str
    output_dir: Path
    repo_root: Path
    source_graph: Path
    snapshot_graph: Path
    dataset: Dataset
    settings: JevSettings
    provenance: RunProvenance
    effective_settings: EffectiveSettings
    started_at: datetime
    tracked_files: frozenset[str]


def _run_git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or "Git command failed."
        raise EvaluationPreflightError(detail[:500])
    return result.stdout


def _optional_git(root: Path, *arguments: str) -> str | None:
    try:
        return _run_git(root, *arguments).strip()
    except EvaluationPreflightError:
        return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _contains(parent: Path, child: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _create_output_dir(
    *, issues_dir: Path, graph: Path, requested: Path | None,
) -> tuple[str, Path]:
    now = datetime.now(UTC)
    run_id = f"{now.strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
    reserved = (Path.cwd() / ".sage/evals").resolve()
    if _contains(reserved, issues_dir) or _contains(reserved, graph):
        raise EvaluationPreflightError("Evaluation inputs cannot be inside .sage/evals.")
    output = (
        requested.expanduser().resolve()
        if requested is not None
        else reserved / "retrieval" / run_id
    )
    if _contains(issues_dir, output) or _contains(output, issues_dir):
        raise EvaluationPreflightError("Output directory cannot overlap the Issue dataset.")
    if _contains(output, graph):
        raise EvaluationPreflightError("Output directory cannot contain the source graph.")
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise EvaluationPreflightError(f"Output directory must be absent or empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    (output / "issues").mkdir()
    return run_id, output


def _snapshot_graph(source: Path, destination: Path) -> None:
    destination_connection: sqlite3.Connection | None = None
    try:
        with GraphStore(source, read_only=True) as store:
            destination_connection = sqlite3.connect(destination)
            store.connection.backup(destination_connection)
            destination_connection.commit()
    except (OSError, ValueError, sqlite3.Error) as error:
        raise EvaluationPreflightError(
            f"Unable to snapshot retrieval graph: {type(error).__name__}: {error}"
        ) from error
    finally:
        if destination_connection is not None:
            destination_connection.close()


def _evaluation_settings(environment: dict[str, str] | None = None) -> JevSettings:
    values = dict(os.environ if environment is None else environment)
    values["SAGE_JEV_NAVIGATION_MODE"] = "on"
    try:
        settings = JevSettings.from_env(values)
    except ConfigurationError as error:
        raise EvaluationPreflightError(str(error)) from error
    if settings.mode != "on":
        raise EvaluationPreflightError("Retrieval evaluation requires Jev mode on.")
    return settings


def prepare_evaluation(
    *,
    repo: Path,
    issues_dir: Path,
    issue_count: int,
    graph: Path,
    output_dir: Path | None = None,
    environment: dict[str, str] | None = None,
) -> PreparedEvaluation:
    """Validate all inputs and create an immutable SQLite snapshot."""

    try:
        dataset = load_dataset(issues_dir, issue_count)
    except DatasetError as error:
        raise EvaluationPreflightError(str(error)) from error
    settings = _evaluation_settings(environment)
    service = build_retrieval_service()
    try:
        repo_root = repo.expanduser().resolve()
        expected_repository_id = service.repository_id(repo_root)
        source_graph = graph.expanduser().resolve()
        stats = service.graph_stats(repo_root=repo_root, index_file=source_graph)
        if stats.status.value != "ready":
            raise EvaluationPreflightError("GRAPH is not a ready retrieval index.")
        with GraphStore(source_graph, read_only=True) as store:
            parser_version = store.get_metadata("parser_version")
            if parser_version != PARSER_VERSION:
                raise EvaluationPreflightError(
                    f"Unsupported parser version {parser_version or 'unknown'}; expected {PARSER_VERSION}."
                )
        if stats.repository_id != expected_repository_id:
            raise EvaluationPreflightError("GRAPH belongs to a different repository.")
    except EvaluationPreflightError:
        raise
    except Exception as error:
        raise EvaluationPreflightError(
            f"Unable to validate repository graph: {type(error).__name__}: {error}"
        ) from error

    raw_tree = _run_git(repo_root, "ls-tree", "-r", "--name-only", "HEAD")
    tracked_files = frozenset(
        normalize_repo_path(value) for value in raw_tree.splitlines() if value
    )
    repository_sha = _run_git(repo_root, "rev-parse", "--verify", "HEAD").strip()
    repository_dirty = bool(_run_git(repo_root, "status", "--porcelain"))
    sage_root = Path.cwd().resolve()
    sage_sha = _optional_git(sage_root, "rev-parse", "--verify", "HEAD")
    sage_dirty = bool(_optional_git(sage_root, "status", "--porcelain")) if sage_sha else None
    graph_source_sha256 = _sha256(source_graph)

    run_id, output = _create_output_dir(
        issues_dir=dataset.issues_dir,
        graph=source_graph,
        requested=output_dir,
    )
    snapshot = output / "graph.sqlite3"
    _snapshot_graph(source_graph, snapshot)
    snapshot_stats = service.graph_stats(repo_root=repo_root, index_file=snapshot)
    if snapshot_stats.repository_id != stats.repository_id or snapshot_stats.indexed_sha != stats.indexed_sha:
        raise EvaluationPreflightError("Graph snapshot provenance changed during backup.")

    provenance = RunProvenance(
        repository=str(repo_root),
        repository_id=stats.repository_id or "",
        repository_sha=repository_sha,
        repository_dirty=repository_dirty,
        graph_source=str(source_graph),
        graph_source_sha256=graph_source_sha256,
        graph_snapshot=str(snapshot),
        graph_snapshot_sha256=_sha256(snapshot),
        schema_version=stats.schema_version or SCHEMA_VERSION,
        parser_version=PARSER_VERSION,
        index_last_updated=stats.last_updated,
        sage_sha=sage_sha,
        sage_dirty=sage_dirty,
        issues_dir=str(dataset.issues_dir),
        correct_sha256=dataset.correct_sha256,
    )
    budgets = RetrievalBudgets(max_chars=STAGING_CHARS)
    effective = EffectiveSettings(
        jev_model=settings.model,
        score_threshold=settings.score_threshold,
        confidence_threshold=settings.confidence_threshold,
        timeout_seconds=settings.timeout_seconds,
        log_input=settings.log_input,
        capture=settings.capture,
        retrieval_max_results=budgets.max_results,
        retrieval_staging_chars=STAGING_CHARS,
        final_context_chars=FINAL_CONTEXT_CHARS,
    )
    return PreparedEvaluation(
        run_id=run_id,
        output_dir=output,
        repo_root=repo_root,
        source_graph=source_graph,
        snapshot_graph=snapshot,
        dataset=dataset,
        settings=settings,
        provenance=provenance,
        effective_settings=effective,
        started_at=datetime.now(UTC),
        tracked_files=tracked_files,
    )


def _base_issue(prepared: PreparedEvaluation, issue: DatasetIssue, *, status: str) -> IssueEvaluation:
    absent = tuple(path for path in issue.gold_files if path not in prepared.tracked_files)
    unsupported = tuple(
        path for path in issue.gold_files
        if path in prepared.tracked_files and detect_language(path) is None
    )
    return IssueEvaluation(
        number=issue.number,
        issue_id=issue.issue_id,
        issue_file=issue.path.name,
        issue_sha256=issue.sha256,
        status=status,
        gold_files=issue.gold_files,
        duplicate_gold_paths=issue.duplicate_gold_paths,
        absent_gold_files=absent,
        unsupported_gold_files=unsupported,
    )


def _results(
    prepared: PreparedEvaluation,
    issues: list[IssueEvaluation],
    *,
    status: str,
    completed_at: datetime | None = None,
) -> EvaluationResults:
    issue_tuple = tuple(issues)
    return EvaluationResults(
        run_id=prepared.run_id,
        status=status,
        started_at=prepared.started_at,
        completed_at=completed_at,
        output_dir=str(prepared.output_dir),
        provenance=prepared.provenance,
        settings=prepared.effective_settings,
        summary=summarize(issue_tuple),
        issues=issue_tuple,
    )


def _checkpoint(
    prepared: PreparedEvaluation,
    issues: list[IssueEvaluation],
    *,
    status: str = "running",
    completed_at: datetime | None = None,
) -> EvaluationResults:
    results = _results(prepared, issues, status=status, completed_at=completed_at)
    write_results(prepared.output_dir, results)
    return results


def _attempt_count(reason: str | None, has_candidates: bool) -> int:
    return int(has_candidates and reason not in {"issue_size", "time_budget", "provider_unavailable"})


def _assert_filter_contract(raw_files: tuple[str, ...], filtered) -> tuple[tuple[str, ...], tuple[str, ...]]:
    report = filtered.relevance_filter
    if report is None or report.mode != "on" or report.status != "filtered":
        raise ValueError("Expected one successful on-mode filtered report.")
    raw_set = set(raw_files)
    rejected = set(report.rejected_files)
    accepted = raw_set - rejected
    final_files = tuple(dict.fromkeys(item.file_path for item in filtered.items))
    final_set = set(final_files)
    if tuple(report.candidate_files) != raw_files:
        raise ValueError("Jev candidate files differ from the raw retrieval shortlist.")
    if not rejected <= raw_set or not final_set <= accepted:
        raise ValueError("Jev accepted/rejected/final sets violate stage containment.")
    if set(report.scores) != raw_set or set(report.confidences) != raw_set:
        raise ValueError("Jev judgments do not cover every candidate file.")
    if set(report.retained_files) != final_set:
        raise ValueError("Reported retained files differ from final context files.")
    if set(report.context_omitted_files) != accepted - final_set:
        raise ValueError("Reported context omissions differ from accepted files omitted by rendering.")
    return tuple(path for path in raw_files if path in accepted), final_files


async def _evaluate_issue(
    prepared: PreparedEvaluation,
    issue: DatasetIssue,
    service,
    relevance_filter: RelevanceFilter,
) -> tuple[IssueEvaluation, dict | None]:
    base = _base_issue(prepared, issue, status="pending")
    raw = service.retrieve_issue_context(
        issue_text=issue.text,
        repo_root=prepared.repo_root,
        index_file=prepared.snapshot_graph,
        budgets=RetrievalBudgets(max_chars=STAGING_CHARS),
    )
    raw_files = tuple(candidate.path for candidate in file_candidates(raw))
    common = {
        "raw_files": raw_files,
        "raw_items": tuple(item.model_dump(mode="json") for item in raw.items),
        "retrieval_status": raw.status.value,
        "retrieval_outcome": raw.outcome.value,
        "retrieval_duration_ms": raw.duration_ms,
    }
    if raw.status == RetrievalStatus.UNAVAILABLE:
        return base.model_copy(update={
            "status": "retrieval_unavailable",
            "reason": raw.summary,
            **common,
        }), None
    if not raw_files:
        return base.model_copy(update={
            "status": "no_candidates",
            "reason": raw.outcome.value,
            "metrics": calculate_metrics(
                gold_files=issue.gold_files,
                raw_files=(),
                accepted_files=(),
                final_files=(),
            ),
            **common,
        }), None

    reports: list[dict] = []
    try:
        filtered = await relevance_filter.apply(
            issue=issue.text,
            retrieval=raw,
            max_chars=FINAL_CONTEXT_CHARS,
            report_writer=reports.append,
        )
    except (asyncio.CancelledError, KeyboardInterrupt) as error:
        report = reports[-1] if reports else {}
        capture = report.get("capture")
        raise IssueEvaluationInterrupted(
            base.model_copy(update={
                "status": "interrupted",
                "reason": "cancelled",
                "withheld_files": tuple(report.get("withheld_files", raw_files)),
                "filter_policy": report.get("policy"),
                "jev_duration_ms": report.get("latency_ms"),
                "jev_input_tokens": report.get("input_tokens"),
                "jev_output_tokens": report.get("output_tokens"),
                "jev_attempts": _attempt_count(report.get("reason"), bool(raw_files)),
                "metrics": calculate_metrics(
                    gold_files=issue.gold_files,
                    raw_files=raw_files,
                    accepted_files=None,
                    final_files=None,
                ),
                **common,
            }),
            capture,
            error,
        ) from error
    report = filtered.relevance_filter
    if report is None:
        raise ValueError("Production relevance filter returned no report.")
    capture = reports[-1].get("capture") if reports else None
    report_fields = {
        "rejected_files": report.rejected_files,
        "withheld_files": report.withheld_files,
        "scores": report.scores,
        "confidences": report.confidences,
        "filter_policy": report.policy,
        "jev_duration_ms": report.latency_ms,
        "jev_input_tokens": report.input_tokens,
        "jev_output_tokens": report.output_tokens,
        "jev_attempts": _attempt_count(report.reason, bool(raw_files)),
    }
    if report.status != "filtered":
        return base.model_copy(update={
            "status": "filter_unavailable",
            "reason": report.reason or report.status,
            "metrics": calculate_metrics(
                gold_files=issue.gold_files,
                raw_files=raw_files,
                accepted_files=None,
                final_files=None,
            ),
            **common,
            **report_fields,
        }), capture

    accepted, final_files = _assert_filter_contract(raw_files, filtered)
    metrics = calculate_metrics(
        gold_files=issue.gold_files,
        raw_files=raw_files,
        accepted_files=accepted,
        final_files=final_files,
    )
    return base.model_copy(update={
        "status": "all_rejected" if not accepted else "judged",
        "accepted_files": accepted,
        "final_files": final_files,
        "metrics": metrics,
        **common,
        **report_fields,
    }), capture


async def run_evaluation(
    prepared: PreparedEvaluation,
    *,
    progress: Progress | None = None,
    service=None,
    relevance_filter: RelevanceFilter | None = None,
) -> tuple[EvaluationResults, int]:
    """Run selected Issues sequentially and checkpoint every terminal record."""

    retrieval_service = service or build_retrieval_service()
    filter_service = relevance_filter or build_relevance_filter(
        prepared.settings, run_id=prepared.run_id
    )
    records = [
        _base_issue(prepared, issue, status="pending") for issue in prepared.dataset.issues
    ]
    for record in records:
        write_issue(prepared.output_dir, record)
    _checkpoint(prepared, records)
    abort_reason: str | None = None
    try:
        for offset, issue in enumerate(prepared.dataset.issues):
            current_sha = _run_git(prepared.repo_root, "rev-parse", "--verify", "HEAD").strip()
            if current_sha != prepared.provenance.repository_sha:
                abort_reason = "repository_head_changed"
                records[offset] = records[offset].model_copy(
                    update={"status": "not_run", "reason": abort_reason}
                )
                break
            try:
                record, capture = await _evaluate_issue(
                    prepared, issue, retrieval_service, filter_service
                )
            except IssueEvaluationInterrupted as interruption:
                records[offset] = interruption.record
                for pending_offset in range(offset + 1, len(records)):
                    if records[pending_offset].status == "pending":
                        records[pending_offset] = records[pending_offset].model_copy(
                            update={"status": "not_run", "reason": "interrupted"}
                        )
                for record in records[offset:]:
                    write_issue(prepared.output_dir, record)
                if interruption.capture is not None:
                    write_json_atomic(
                        prepared.output_dir / "issues" / f"issue-{issue.number}.jev-capture.json",
                        interruption.capture,
                    )
                _checkpoint(prepared, records, status="partial", completed_at=datetime.now(UTC))
                if progress is not None:
                    progress.update(1)
                    progress.set_postfix_str(f"issue={issue.number} interrupted=1")
                raise interruption.cause
            except Exception as error:
                logger.exception("Evaluation failed for %s", issue.path.name)
                records[offset] = records[offset].model_copy(update={
                    "status": "filter_unavailable",
                    "reason": f"evaluation_contract: {type(error).__name__}: {str(error)[:300]}",
                })
                abort_reason = "evaluation_contract"
                capture = None
            else:
                records[offset] = record
            write_issue(prepared.output_dir, records[offset])
            if capture is not None:
                write_json_atomic(
                    prepared.output_dir / "issues" / f"issue-{issue.number}.jev-capture.json",
                    capture,
                )
            _checkpoint(prepared, records)
            if progress is not None:
                progress.update(1)
                failures = sum(
                    item.status in {"filter_unavailable", "retrieval_unavailable"}
                    for item in records
                )
                successes = sum(item.status in {"judged", "all_rejected"} for item in records)
                progress.set_postfix_str(
                    f"issue={issue.number} judged={successes} failures={failures}"
                )
            if records[offset].reason in _FATAL_FILTER_REASONS:
                abort_reason = records[offset].reason
            if abort_reason:
                break
    finally:
        await filter_service.aclose()

    if abort_reason:
        records = [
            item.model_copy(update={"status": "not_run", "reason": abort_reason})
            if item.status == "pending" else item
            for item in records
        ]
        for record in records:
            if record.status == "not_run":
                write_issue(prepared.output_dir, record)
    complete = all(
        item.status in {"judged", "all_rejected", "no_candidates"} for item in records
    )
    status = "complete" if complete else "partial"
    results = _checkpoint(
        prepared,
        records,
        status=status,
        completed_at=datetime.now(UTC),
    )
    return results, 0 if complete else 1
