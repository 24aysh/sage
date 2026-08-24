from pathlib import Path

from sage.integrations.github.commands import SageCommand
from sage.integrations.github.provenance import (
    GitHubProvenance,
    persist_github_diagnostics,
)


def test_github_diagnostics_copy_safe_admission_summary_not_full_context(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "admission-context-summary.json").write_text(
        '{"context_digest":"safe"}\n',
        encoding="utf-8",
    )
    (run_dir / "admission-context.json").write_text(
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
        outcome="needs_human_information",
    )

    persist_github_diagnostics(
        provenance,
        diagnostics_dir=diagnostics,
        run_dir=run_dir,
    )

    assert (diagnostics / "admission-context-summary.json").is_file()
    assert not (diagnostics / "admission-context.json").exists()
    assert "private repository text" not in "\n".join(
        path.read_text(encoding="utf-8") for path in diagnostics.iterdir()
    )
