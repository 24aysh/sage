"""Atomic machine-readable, Markdown, and terminal evaluation reports."""

from __future__ import annotations

from pathlib import Path

from sage.artifacts.files import write_json_atomic, write_text_atomic

from evals.retrieval.models import EvaluationResults, IssueEvaluation


def write_issue(output_dir: Path, issue: IssueEvaluation) -> None:
    write_json_atomic(
        output_dir / "issues" / f"issue-{issue.number}.json",
        issue.model_dump(mode="json"),
    )


def write_results(output_dir: Path, results: EvaluationResults) -> None:
    write_json_atomic(output_dir / "results.json", results.model_dump(mode="json"))
    write_text_atomic(output_dir / "evals.md", render_markdown(results))


def _number(value: float | None, suffix: str = "%") -> str:
    return "N/A" if value is None else f"{value:.2f}{suffix}"


def _metric_line(label: str, metric, suffix: str = "%") -> str:
    return (
        f"- {label}: {_number(metric.mean, suffix)} "
        f"({metric.eligible} eligible, {metric.excluded} excluded)"
    )


def _cell(value: object) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _paths(values: tuple[str, ...]) -> str:
    return ", ".join(f"`{value.replace('`', '')}`" for value in values) or "—"


def render_markdown(results: EvaluationResults) -> str:
    summary = results.summary
    lines = [
        "# Retrieval noise evaluation",
        "",
        f"- Status: **{results.status}**",
        f"- Run: `{results.run_id}`",
        f"- Repository: `{results.provenance.repository}` at `{results.provenance.repository_sha}`",
        f"- Index snapshot: `{results.provenance.graph_snapshot}`",
        f"- Dataset: `{results.provenance.issues_dir}`",
        f"- Jev: `{results.settings.jev_model}` in `on` mode; score ≥ "
        f"{results.settings.score_threshold}, confidence ≥ {results.settings.confidence_threshold}",
        "- Label semantics: noise is relative to files labeled as requiring modification; "
        "useful background files can therefore count as noise.",
        "",
        "## Aggregate metrics",
        "",
        _metric_line("Average noise reduction", summary.average_noise_reduction_pp, " percentage points"),
        _metric_line(
            "Average noise before on the paired reduction population",
            summary.average_noise_before_pct,
        ),
        _metric_line(
            "Average noise after Jev on the paired reduction population",
            summary.average_noise_after_jev_pct,
        ),
        _metric_line("Average retain (all gold files denominator)", summary.average_retain_pct),
        _metric_line(
            "Average retrieved-correct survival",
            summary.average_retrieved_correct_survival_pct,
        ),
        _metric_line("Average raw correct recall", summary.average_raw_correct_recall_pct),
        _metric_line("Average post-Jev correct recall", summary.average_post_jev_correct_recall_pct),
        f"- Irrelevant files removed: {summary.total_irrelevant_files_removed}",
        f"- Counts: `{summary.counts}`",
        "",
        "Undefined empty-set and failed-call metrics are shown as N/A and excluded from their named averages.",
        "",
        "## Per-Issue metrics",
        "",
        "| Issue | Status | Gold | Raw / correct | Jev / correct | Noise before | Noise after | Reduction pp | Correct drops | Retain | Survival | Raw recall | Post recall | Final / correct | Final recall | Jev ms | Tokens in/out |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for issue in results.issues:
        metric = issue.metrics
        lines.append("| " + " | ".join((
            f"[{issue.issue_file}](issues/issue-{issue.number}.json)",
            _cell(issue.status),
            _cell(metric.gold_count if metric else len(issue.gold_files)),
            _cell(f"{metric.raw_count} / {metric.raw_correct_count}" if metric else None),
            _cell(
                f"{metric.accepted_count} / {metric.accepted_correct_count}"
                if metric and metric.accepted_count is not None else None
            ),
            _cell(metric.noise_before_pct if metric else None),
            _cell(metric.noise_after_jev_pct if metric else None),
            _cell(metric.noise_reduction_pp if metric else None),
            _cell(len(metric.correct_files_dropped) if metric else None),
            _cell(metric.retain_pct if metric else None),
            _cell(metric.retrieved_correct_survival_pct if metric else None),
            _cell(metric.raw_correct_recall_pct if metric else None),
            _cell(metric.post_jev_correct_recall_pct if metric else None),
            _cell(
                f"{metric.final_count} / {metric.final_correct_count}"
                if metric and metric.final_count is not None else None
            ),
            _cell(metric.final_context_correct_recall_pct if metric else None),
            _cell(issue.jev_duration_ms),
            _cell(
                f"{issue.jev_input_tokens} / {issue.jev_output_tokens}"
                if issue.jev_input_tokens is not None else None
            ),
        )) + " |")
    lines.extend(["", "## Per-Issue path evidence", ""])
    for issue in results.issues:
        metric = issue.metrics
        lines.extend([
            f"### {issue.issue_file}",
            "",
            f"- Reason: {_cell(issue.reason)}",
            f"- Raw candidates: {_paths(issue.raw_files)}",
            f"- Accepted by Jev: {_paths(issue.accepted_files)}",
            f"- Missed correct files: {_paths(metric.retrieval_misses if metric else ())}",
            f"- Correct files dropped by Jev: {_paths(metric.correct_files_dropped if metric else ())}",
            f"- Irrelevant files removed: {_paths(metric.irrelevant_files_removed if metric else ())}",
            f"- Accepted but omitted by context budget: {_paths(metric.context_omitted_files if metric else ())}",
            f"- Gold files absent from indexed commit: {_paths(issue.absent_gold_files)}",
            f"- Gold files unsupported by the parser: {_paths(issue.unsupported_gold_files)}",
            "",
        ])
    return "\n".join(lines).rstrip() + "\n"


def terminal_summary(results: EvaluationResults) -> str:
    summary = results.summary
    noise, retain, survival = (
        summary.average_noise_reduction_pp,
        summary.average_retain_pct,
        summary.average_retrieved_correct_survival_pct,
    )
    return "\n".join((
        f"Average noise reduction: {_number(noise.mean, ' percentage points')} "
        f"({noise.eligible} eligible, {noise.excluded} excluded)",
        f"Average retain: {_number(retain.mean)} "
        f"({retain.eligible} eligible, {retain.excluded} excluded; denominator: all gold files)",
        f"Average retrieved-correct survival: {_number(survival.mean)} "
        f"({survival.eligible} eligible, {survival.excluded} excluded)",
        f"Irrelevant files removed: {summary.total_irrelevant_files_removed}",
        f"Report: {Path(results.output_dir) / 'evals.md'}",
    ))
