"""Pure set metrics and explicit macro-average eligibility."""

from __future__ import annotations

import statistics
from collections import Counter
from collections.abc import Callable, Iterable

from evals.retrieval.models import AggregateMetric, EvaluationSummary, IssueEvaluation, IssueMetrics


def _pct(numerator: int, denominator: int) -> float | None:
    return 100.0 * numerator / denominator if denominator else None


def _noise(gold: set[str], observed: set[str]) -> float | None:
    return 100.0 * (1.0 - len(gold & observed) / len(observed)) if observed else None


def calculate_metrics(
    *,
    gold_files: Iterable[str],
    raw_files: Iterable[str],
    accepted_files: Iterable[str] | None,
    final_files: Iterable[str] | None,
) -> IssueMetrics:
    """Calculate metrics without assigning provider failures synthetic outcomes."""

    gold, raw = set(gold_files), set(raw_files)
    raw_correct = gold & raw
    misses = gold - raw
    if accepted_files is None:
        return IssueMetrics(
            gold_count=len(gold),
            raw_count=len(raw),
            raw_correct_count=len(raw_correct),
            noise_before_pct=_noise(gold, raw),
            raw_correct_recall_pct=_pct(len(raw_correct), len(gold)),
            retrieval_misses=tuple(sorted(misses)),
        )

    accepted = set(accepted_files)
    final = set(final_files or ())
    dropped = raw_correct - accepted
    irrelevant_removed = (raw - gold) - accepted
    raw_noise = raw - gold
    accepted_correct = gold & accepted
    final_correct = gold & final
    before, after = _noise(gold, raw), _noise(gold, accepted)
    return IssueMetrics(
        gold_count=len(gold),
        raw_count=len(raw),
        raw_correct_count=len(raw_correct),
        accepted_count=len(accepted),
        accepted_correct_count=len(accepted_correct),
        final_count=len(final),
        final_correct_count=len(final_correct),
        noise_before_pct=before,
        noise_after_jev_pct=after,
        noise_after_final_pct=_noise(gold, final),
        noise_reduction_pp=(before - after) if before is not None and after is not None else None,
        noise_files_removed=len(irrelevant_removed),
        noise_removed_pct=_pct(len(irrelevant_removed), len(raw_noise)),
        retain_pct=(100.0 * (1.0 - len(dropped) / len(gold))) if raw else None,
        retrieved_correct_survival_pct=_pct(len(accepted_correct), len(raw_correct)),
        raw_correct_recall_pct=_pct(len(raw_correct), len(gold)),
        post_jev_correct_recall_pct=_pct(len(accepted_correct), len(gold)),
        final_context_correct_recall_pct=_pct(len(final_correct), len(gold)),
        retrieval_misses=tuple(sorted(misses)),
        correct_files_dropped=tuple(sorted(dropped)),
        irrelevant_files_removed=tuple(sorted(irrelevant_removed)),
        context_omitted_files=tuple(sorted(accepted - final)),
    )


def _aggregate(
    issues: tuple[IssueEvaluation, ...],
    getter: Callable[[IssueMetrics], float | None],
) -> AggregateMetric:
    values: list[float] = []
    reasons: Counter[str] = Counter()
    for issue in issues:
        if issue.metrics is None:
            reasons[issue.status] += 1
            continue
        value = getter(issue.metrics)
        if value is None:
            reasons[f"{issue.status}:undefined"] += 1
        else:
            values.append(value)
    return AggregateMetric(
        mean=statistics.fmean(values) if values else None,
        eligible=len(values),
        excluded=len(issues) - len(values),
        exclusion_reasons=dict(sorted(reasons.items())),
    )


def summarize(issues: tuple[IssueEvaluation, ...]) -> EvaluationSummary:
    counts = Counter(issue.status for issue in issues)
    for status in (
        "pending", "judged", "all_rejected", "no_candidates", "filter_unavailable",
        "retrieval_unavailable", "interrupted", "not_run",
    ):
        counts.setdefault(status, 0)
    counts["requested"] = len(issues)
    counts["processed"] = sum(issue.status not in {"pending", "not_run"} for issue in issues)
    counts["successfully_judged"] = counts["judged"] + counts["all_rejected"]
    return EvaluationSummary(
        counts=dict(sorted(counts.items())),
        average_noise_before_pct=_aggregate(issues, lambda metric: (
            metric.noise_before_pct if metric.noise_reduction_pp is not None else None
        )),
        average_noise_after_jev_pct=_aggregate(issues, lambda metric: (
            metric.noise_after_jev_pct if metric.noise_reduction_pp is not None else None
        )),
        average_noise_reduction_pp=_aggregate(issues, lambda metric: metric.noise_reduction_pp),
        average_retain_pct=_aggregate(issues, lambda metric: metric.retain_pct),
        average_retrieved_correct_survival_pct=_aggregate(
            issues, lambda metric: metric.retrieved_correct_survival_pct
        ),
        average_raw_correct_recall_pct=_aggregate(issues, lambda metric: (
            metric.raw_correct_recall_pct
            if issue_has_valid_retrieval(metric) else None
        )),
        average_post_jev_correct_recall_pct=_aggregate(
            issues, lambda metric: metric.post_jev_correct_recall_pct
        ),
        total_irrelevant_files_removed=sum(
            issue.metrics.noise_files_removed or 0 for issue in issues if issue.metrics
        ),
    )


def issue_has_valid_retrieval(metric: IssueMetrics) -> bool:
    """A metric record is created only after a valid retrieval observation."""

    return metric.raw_correct_recall_pct is not None
