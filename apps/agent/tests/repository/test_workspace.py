import subprocess
from pathlib import Path

import pytest

from sage.config import Settings
from sage.domain.requests import SolveRequest
from sage.errors import HostGitError, HostGitTimeoutError, WorkspaceError
from sage.repository.workspace import prepare_run


def test_prepare_run_clones_only_committed_revision(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init")
    _git(source, "config", "user.email", "test@example.com")
    _git(source, "config", "user.name", "Test User")
    tracked = source / "tracked.txt"
    tracked.write_text("committed\n", encoding="utf-8")
    _git(source, "add", "tracked.txt")
    _git(source, "commit", "-m", "initial")
    expected_sha = _git(source, "rev-parse", "HEAD").stdout.strip()

    tracked.write_text("uncommitted\n", encoding="utf-8")
    (source / "untracked.txt").write_text("ignored\n", encoding="utf-8")
    issue = tmp_path / "issue.md"
    issue.write_text("Fix the behavior.", encoding="utf-8")
    settings = Settings(openai_api_key="test", runs_dir=tmp_path / "runs")

    prepared = prepare_run(
        SolveRequest(repo_path=source, issue_path=issue),
        settings,
    )

    assert prepared.source_repo == source.resolve()
    assert prepared.workspace_dir != source.resolve()
    assert prepared.base_sha == expected_sha
    assert (prepared.workspace_dir / "tracked.txt").read_text() == "committed\n"
    assert not (prepared.workspace_dir / "untracked.txt").exists()
    assert tracked.read_text() == "uncommitted\n"


@pytest.mark.parametrize(
    ("host_error", "message"),
    [
        (HostGitError("missing"), "Git executable was not found"),
        (HostGitTimeoutError("slow"), "Git workspace preparation timed out"),
    ],
)
def test_prepare_run_preserves_host_git_failure_messages(
    tmp_path: Path,
    monkeypatch,
    host_error: HostGitError,
    message: str,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    issue = tmp_path / "issue.md"
    issue.write_text("Fix it.", encoding="utf-8")

    def fail_git(*args, **kwargs):
        raise host_error

    monkeypatch.setattr("sage.repository.workspace.run_git", fail_git)

    with pytest.raises(WorkspaceError, match=message):
        prepare_run(
            SolveRequest(repo_path=source, issue_path=issue),
            Settings(openai_api_key="test", runs_dir=tmp_path / "runs"),
        )


def _git(repository: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
