import json
from pathlib import Path

from sage.artifacts.store import RunArtifacts
from sage.config import Settings
from sage.domain.solve import AgentFinalOutput, PreparedRun, SolveRequest, SolveResult


def test_run_artifacts_persists_required_files_without_secret(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    workspace = run_dir / "repo"
    workspace.mkdir()
    issue_path = tmp_path / "issue.md"
    issue_path.write_text("Issue contents", encoding="utf-8")
    request = SolveRequest(repo_path=tmp_path, issue_path=issue_path)
    prepared = PreparedRun(
        run_id="run-id",
        source_repo=tmp_path,
        run_dir=run_dir,
        workspace_dir=workspace,
        base_ref="HEAD",
        base_sha="a" * 40,
    )
    settings = Settings(openai_api_key="must-not-persist")
    final = AgentFinalOutput(summary="Fixed it.", changed_files_claimed=["file.py"])
    result = SolveResult(
        run_id="run-id",
        base_sha="a" * 40,
        summary="Fixed it.",
        remaining_uncertainty=[],
        changed_files=["file.py"],
        diff="diff contents\n",
        run_dir=run_dir,
        workspace_dir=workspace,
    )
    store = RunArtifacts(run_dir)

    store.initialize(
        request=request,
        prepared_run=prepared,
        issue_text="Issue contents",
        settings=settings,
    )
    store.write_result(final_output=final, result=result)

    expected = {
        "request.json",
        "metadata.json",
        "issue.md",
        "agent-final.json",
        "changed-files.json",
        "diff.patch",
        "repo",
    }
    assert {path.name for path in run_dir.iterdir()} == expected
    all_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in run_dir.iterdir()
        if path.is_file()
    )
    assert "must-not-persist" not in all_text
    assert "memory_file" not in json.loads(
        (run_dir / "request.json").read_text(encoding="utf-8")
    )
    assert json.loads((run_dir / "changed-files.json").read_text()) == ["file.py"]
