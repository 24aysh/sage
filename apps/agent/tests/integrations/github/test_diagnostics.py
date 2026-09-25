import json
from pathlib import Path

import pytest

from sage.errors import ArtifactError
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
    (run_dir / "relevance-filter.json").write_text(json.dumps({"policy": "file-relevance-v1",
        "mode": "on", "status": "filtered", "model": "jev-1.13.0", "score_threshold": 2,
        "confidence_threshold": .5, "candidate_items": 2, "discarded_items": 1,
        "candidate_files": ["private.py", "other.py"], "retained_files": ["private.py"],
        "rejected_files": ["other.py"], "capture": {"request": "private Jev replay"}}))
    (run_dir / "usage.json").write_text('{"semantic_calls":[{"model":"jev-1.13.0","input_tokens":23}]}')
    (run_dir / "repository-retrieval.json").write_text(
        '{"status":"used","retrieval":{"context":"private source body",'
        '"context_chars":19}}\n',
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
    retrieval = (diagnostics / "repository-retrieval.json").read_text(encoding="utf-8")
    assert '"status": "used"' in retrieval
    assert '"context_chars": 19' in retrieval
    assert "private source body" not in retrieval
    assert not (diagnostics / "unlisted-context.json").exists()
    navigation = json.loads((diagnostics / "relevance-filter.json").read_text())
    assert navigation["policy"] == "file-relevance-v1"
    assert navigation["candidate_files_count"] == 2
    assert navigation["discarded_items"] == 1
    assert "private Jev replay" not in json.dumps(navigation)
    assert "private.py" not in json.dumps(navigation)
    assert '"semantic_calls"' in (diagnostics / "usage.json").read_text()
    assert "private repository text" not in "\n".join(
        path.read_text(encoding="utf-8") for path in diagnostics.iterdir()
    )


def test_github_diagnostics_reject_invalid_retrieval_artifact(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "repository-retrieval.json").write_text("not-json", encoding="utf-8")

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

    with pytest.raises(ArtifactError, match="Invalid Repository retrieval index"):
        persist_github_diagnostics(
            provenance,
            diagnostics_dir=tmp_path / "diagnostics",
            run_dir=run_dir,
        )


def test_github_diagnostics_reject_invalid_relevance_artifact(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "relevance-filter.json").write_text('{"records":"not-a-list"}', encoding="utf-8")
    provenance = GitHubProvenance(repository="owner/repository", repository_id=1, issue_number=2,
        invocation_comment_id=3, actor="maintainer", actions_run_id=4, actions_run_attempt=1,
        actions_run_url="https://github.com/owner/repository/actions/runs/4", command=SageCommand.SOLVE,
        base_branch="main", original_base_sha="a" * 40, branch="sage/issue-2", outcome="completed")

    with pytest.raises(ArtifactError, match="Invalid Jev relevance"):
        persist_github_diagnostics(provenance, diagnostics_dir=tmp_path / "diagnostics", run_dir=run_dir)
