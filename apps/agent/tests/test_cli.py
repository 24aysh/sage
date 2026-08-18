from pathlib import Path

from sage.cli import _build_parser, _render_result
from sage.domain.results import SolveResult


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
    assert capsys.readouterr().out.startswith("Sage V0\n")
