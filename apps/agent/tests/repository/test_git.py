import subprocess
from pathlib import Path

from sage.repository.git import get_changed_files, get_complete_diff
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


def _git(repository: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
