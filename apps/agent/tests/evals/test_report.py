from datetime import UTC, datetime
from pathlib import Path

from evals.retrieval.metrics import calculate_metrics, summarize
from evals.retrieval.models import (
    EffectiveSettings,
    EvaluationResults,
    IssueEvaluation,
    RunProvenance,
)
from evals.retrieval.report import (
    headline_summary_lines,
    render_markdown,
    terminal_summary,
    write_results,
)


def _results(tmp_path: Path) -> EvaluationResults:
    metric = calculate_metrics(
        gold_files={"src/a.py"},
        raw_files={"src/a.py", "src/noise.py"},
        accepted_files={"src/a.py"},
        final_files={"src/a.py"},
    )
    issue = IssueEvaluation(
        number=1,
        issue_id="issue_1",
        issue_file="issue-1.md",
        issue_sha256="0" * 64,
        status="judged",
        gold_files=("src/a.py",),
        raw_files=("src/a.py", "src/noise.py"),
        accepted_files=("src/a.py",),
        final_files=("src/a.py",),
        metrics=metric,
    )
    return EvaluationResults(
        run_id="run",
        status="complete",
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
        output_dir=str(tmp_path),
        provenance=RunProvenance(
            repository="/repo",
            repository_id="id",
            repository_sha="sha",
            repository_dirty=False,
            graph_source="/graph",
            graph_source_sha256="source-hash",
            graph_snapshot="/snapshot",
            graph_snapshot_sha256="hash",
            schema_version=1,
            parser_version="parser",
            issues_dir="/issues",
            correct_sha256="labels",
        ),
        settings=EffectiveSettings(
            jev_model="jev",
            score_threshold=2,
            confidence_threshold=.5,
            timeout_seconds=2,
            log_input=False,
            capture=False,
            retrieval_max_results=12,
            retrieval_staging_chars=50_000,
            final_context_chars=12_000,
        ),
        summary=summarize((issue,)),
        issues=(issue,),
    )


def test_markdown_and_terminal_use_the_same_aggregate(tmp_path: Path) -> None:
    results = _results(tmp_path)
    markdown = render_markdown(results)
    terminal = terminal_summary(results)

    assert "Average noise reduction: 50.00 percentage points" in terminal
    assert "Average noise reduction: 50.00 percentage points" in markdown
    for line in headline_summary_lines(results):
        assert line in terminal
        assert line in markdown
    assert "src/noise.py" in markdown


def test_reports_are_written_atomically_as_json_and_markdown(tmp_path: Path) -> None:
    results = _results(tmp_path)
    write_results(tmp_path, results)

    assert (tmp_path / "results.json").read_text().endswith("\n")
    assert (tmp_path / "evals.md").read_text().startswith("# Retrieval noise evaluation")
