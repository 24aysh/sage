from pathlib import Path

from sage.cli import app as cli
from sage.cli.app import _build_parser
from sage.cli.output import _render_result
from sage.domain.solve import SolveResult


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
