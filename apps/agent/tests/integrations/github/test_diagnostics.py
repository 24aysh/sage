from pathlib import Path

from sage.integrations.github.diagnostics import (
    GitHubProvenance,
    persist_github_diagnostics,
)
from sage.integrations.github.models import SageCommand


def test_github_diagnostics_copy_only_allowlisted_run_artifacts(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "solver-final.json").write_text(
        '{"summary":"safe"}\n',
        encoding="utf-8",
    )
    (run_dir / "unlisted-context.json").write_text(
        '{"excerpt":"private repository text"}\n',
        encoding="utf-8",
    )
    diagnostics = tmp_path / "diagnostics"
    provenance = GitHubProvenance(
        repository="owner/repository",
        repository_id=1,
        issue_number=2,
        invocation_comment_id=3,
        actor="maintainer",
        actions_run_id=4,
        actions_run_attempt=1,
        actions_run_url="https://github.com/owner/repository/actions/runs/4",
        command=SageCommand.SOLVE,
        base_branch="main",
        original_base_sha="a" * 40,
        branch="sage/issue-2",
        outcome="completed",
    )

    persist_github_diagnostics(
        provenance,
        diagnostics_dir=diagnostics,
        run_dir=run_dir,
    )

    assert (diagnostics / "solver-final.json").is_file()
    assert not (diagnostics / "unlisted-context.json").exists()
    assert "private repository text" not in "\n".join(
        path.read_text(encoding="utf-8") for path in diagnostics.iterdir()
    )
