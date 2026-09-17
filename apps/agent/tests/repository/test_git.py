import subprocess
from pathlib import Path

import pytest

from sage.errors import RepositoryError
from sage.repository.git import (
    get_changed_files,
    get_complete_diff,
    list_branches,
    switch_branch,
)
from sage.sandbox.base import CommandResult


class LocalGitSandbox:
    """Test double that executes only against a temporary test repository."""

    def __init__(self, repository: Path) -> None:
        self.repository = repository

    def start(self) -> None:
        pass

    def exec(
        self,
        command: str,
        *,
        timeout_seconds: int | None = None,
    ) -> CommandResult:
        completed = subprocess.run(
            command,
            cwd=self.repository,
            shell=True,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        return CommandResult(
            command=command,
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )

    def stop(self) -> None:
        pass


def test_git_diff_includes_new_untracked_files(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test User")
    (tmp_path / "existing.txt").write_text("existing\n", encoding="utf-8")
    _git(tmp_path, "add", "existing.txt")
    _git(tmp_path, "commit", "-m", "initial")
    (tmp_path / "new.txt").write_text("new content\n", encoding="utf-8")
    sandbox = LocalGitSandbox(tmp_path)

    diff = get_complete_diff(sandbox, timeout_seconds=10)
    changed_files = get_changed_files(sandbox, timeout_seconds=10)

    assert "new file mode" in diff
    assert "+new content" in diff
    assert changed_files == ["new.txt"]


def test_list_and_switch_branches_in_clean_sandbox(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test User")
    (tmp_path / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    _git(tmp_path, "add", "tracked.txt")
    _git(tmp_path, "commit", "-m", "initial")
    original_branch = _git_output(tmp_path, "branch", "--show-current")
    _git(tmp_path, "branch", "feature/test")
    sandbox = LocalGitSandbox(tmp_path)

    branches = list_branches(
        sandbox,
        max_output_chars=10_000,
        timeout_seconds=10,
    )
    result = switch_branch(
        sandbox,
        branch_name="feature/test",
        timeout_seconds=10,
    )

    assert original_branch in branches
    assert "feature/test" in branches
    assert result == "Switched to branch feature/test."
    assert _git_output(tmp_path, "branch", "--show-current") == "feature/test"


def test_switch_branch_rejects_dirty_worktree(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test User")
    (tmp_path / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    _git(tmp_path, "add", "tracked.txt")
    _git(tmp_path, "commit", "-m", "initial")
    original_branch = _git_output(tmp_path, "branch", "--show-current")
    _git(tmp_path, "branch", "feature/test")
    (tmp_path / "tracked.txt").write_text("changed\n", encoding="utf-8")

    with pytest.raises(RepositoryError, match="uncommitted changes"):
        switch_branch(
            LocalGitSandbox(tmp_path),
            branch_name="feature/test",
            timeout_seconds=10,
        )

    assert _git_output(tmp_path, "branch", "--show-current") == original_branch


def test_switch_branch_rejects_invalid_ref_without_shell_side_effect(
    tmp_path: Path,
) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test User")
    (tmp_path / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    _git(tmp_path, "add", "tracked.txt")
    _git(tmp_path, "commit", "-m", "initial")

    with pytest.raises(RepositoryError, match="check-ref-format"):
        switch_branch(
            LocalGitSandbox(tmp_path),
            branch_name="feature; touch unexpected",
            timeout_seconds=10,
        )

    assert not (tmp_path / "unexpected").exists()


def _git(repository: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )


def _git_output(repository: Path, *arguments: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repository), *arguments],
        text=True,
    ).strip()
