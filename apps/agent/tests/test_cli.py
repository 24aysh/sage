from pathlib import Path

import pytest

from sage.cli import app as cli
from sage.cli.app import _build_parser
from sage.cli.output import _render_result
from sage.domain.solve import SolveOutcome, SolveResult
from sage.domain.usage import AgentTimingRecord, ModelCallRecord, RunProvenance, SemanticCallRecord


def test_cli_uses_sage_name(capsys, tmp_path: Path) -> None:
    parser = _build_parser()
    result = SolveResult(
        run_id="run-id",
        base_sha="a" * 40,
        summary="No change required.",
        remaining_uncertainty=[],
        changed_files=[],
        diff="",
        run_dir=tmp_path,
        workspace_dir=tmp_path / "repo",
    )

    _render_result(result, model="test-model")

    assert parser.prog == "sage"
    assert capsys.readouterr().out.startswith("Sage\n")


def test_langsmith_traces_are_flushed_only_when_enabled(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(cli, "wait_for_all_tracers", lambda: calls.append("flush"))
    monkeypatch.setenv("LANGSMITH_TRACING", "false")

    cli._flush_langsmith_traces()
    assert calls == []

    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    cli._flush_langsmith_traces()
    assert calls == ["flush"]


@pytest.mark.parametrize("duration,expected", [(125500, "125.50 seconds"), (0, "0.00 seconds"),
                                               (None, "unavailable")])
@pytest.mark.parametrize("outcome", [SolveOutcome.COMPLETED, SolveOutcome.NO_CHANGE])
def test_final_summary_prints_total_solve_time(capsys, tmp_path, duration, expected, outcome):
    result = SolveResult(run_id="run-id", base_sha="a" * 40, summary="Finished.",
        remaining_uncertainty=[], changed_files=["app.py"] if outcome is SolveOutcome.COMPLETED else [],
        diff="diff" if outcome is SolveOutcome.COMPLETED else "", run_dir=tmp_path,
        workspace_dir=tmp_path / "repo", outcome=outcome, workflow_duration_ms=duration)
    _render_result(result, model="test-model")
    assert capsys.readouterr().out.endswith(f"Total solve time: {expected}\n")


@pytest.mark.parametrize("jev_enabled", [False, True])
def test_agent_time_totals_include_repairs_and_failed_jev_attempts(capsys, tmp_path, jev_enabled):
    provenance = RunProvenance(agent_timings=(
        AgentTimingRecord(role="solver", stage="solver", duration_ms=100000),
        AgentTimingRecord(role="reviewer", stage="review", duration_ms=12000),
        AgentTimingRecord(role="solver", stage="solver-repair", duration_ms=50000),
        AgentTimingRecord(role="reviewer", stage="rereview", duration_ms=8000)),
        semantic_calls=tuple(SemanticCallRecord(call_number=i, session=i, stage="solver",
            policy="actions", model="jev", latency_ms=ms, outcome=outcome)
            for i, ms, outcome in [(1, 1000, "decided"), (2, 500, "timeout")]) if jev_enabled else ())
    result = SolveResult(run_id="run", base_sha="a" * 40, summary="Done", remaining_uncertainty=[],
        changed_files=["app.py"], diff="diff", run_dir=tmp_path, workspace_dir=tmp_path,
        provenance=provenance, workflow_duration_ms=185000)
    _render_result(result, model="test-model")
    output = capsys.readouterr().out
    assert "Solver (including Jev): 150.00 seconds" in output
    assert f"Solver (without Jev): {'148.50' if jev_enabled else '150.00'} seconds" in output
    assert f"Jev (decisions only): {'1.50' if jev_enabled else '0.00'} seconds" in output
    assert "Reviewer: 20.00 seconds" in output
    assert output.endswith("Total solve time: 185.00 seconds\n")


@pytest.mark.parametrize("timings,expected", [(None, "unavailable"), ((), "0.00 seconds")])
def test_unavailable_agent_timings_differ_from_agents_not_invoked(capsys, tmp_path, timings, expected):
    result = SolveResult(run_id="run", base_sha="a" * 40, summary="Done", remaining_uncertainty=[],
        changed_files=[], diff="", run_dir=tmp_path, workspace_dir=tmp_path,
        provenance=RunProvenance(agent_timings=timings))
    _render_result(result, model="test-model")
    output = capsys.readouterr().out
    for label in ("Solver (including Jev)", "Solver (without Jev)", "Jev (decisions only)", "Reviewer"):
        assert f"{label}: {expected}" in output


def test_interrupted_summary_reports_known_tokens_including_jev_without_claiming_completion(capsys, tmp_path):
    usage = RunProvenance(agent_timings=(AgentTimingRecord(role="solver", stage="solver", duration_ms=3000),),
        calls=(ModelCallRecord(call_number=1, stage="solver", role="solver", attempt_kind="primary",
            provider="openai", model="solver", latency_ms=100, input_tokens=20, output_tokens=5, outcome="success"),),
        semantic_calls=(
            SemanticCallRecord(call_number=1, session=1, stage="solver", policy="actions", model="jev",
                latency_ms=500, outcome="decided", input_tokens=10, output_tokens=2),
            SemanticCallRecord(call_number=2, session=1, stage="solver", policy="actions", model="jev",
                latency_ms=1000, outcome="cancelled")))
    result = SolveResult(run_id="interrupted-run", base_sha="a" * 40, summary="Interrupted",
        remaining_uncertainty=[], changed_files=[], diff="", run_dir=tmp_path, workspace_dir=tmp_path,
        outcome=SolveOutcome.INTERRUPTED, provenance=usage, workflow_duration_ms=5500)
    _render_result(result, model="solver")
    output = capsys.readouterr().out
    assert "Solve interrupted" in output and "Agent completed" not in output
    assert "Jev calls: 2" in output and "Total tokens: 37" in output
    assert "Token usage incomplete: 1 call(s)" in output
    assert "Solver (including Jev): 3.00 seconds" in output
    assert "Solver (without Jev): 1.50 seconds" in output
    assert "Elapsed time at interruption: 5.50 seconds" in output
    assert "unreported in-flight tokens may still be billed" in output
