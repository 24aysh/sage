from __future__ import annotations

import subprocess
from pathlib import Path

from sage.repository.scout import RepositoryScout
from sage.sandbox.base import CommandResult


class NoMatchSandbox:
    def exec(self, command: str, *, timeout_seconds: int | None = None) -> CommandResult:
        del timeout_seconds
        return CommandResult(command=command, exit_code=1, stdout="", stderr="")


def test_scout_prioritizes_exact_paths_without_mutating_repository(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True)
    source = tmp_path / "src" / "widget.py"
    test = tmp_path / "tests" / "test_widget.py"
    source.parent.mkdir()
    test.parent.mkdir()
    source.write_text("def widget():\n    return 1\n", encoding="utf-8")
    test.write_text("from src.widget import widget\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "src/widget.py", "tests/test_widget.py"],
        cwd=tmp_path,
        check=True,
    )
    before = subprocess.run(
        ["git", "status", "--porcelain=v1"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout

    repository_map = RepositoryScout(
        workspace=tmp_path,
        sandbox=NoMatchSandbox(),  # type: ignore[arg-type]
        max_output_chars=12_000,
        timeout_seconds=10,
    ).scout(issue_text="Update src/widget.py widget behavior.", base_sha="a" * 40)

    after = subprocess.run(
        ["git", "status", "--porcelain=v1"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert repository_map.exact_issue_paths == ("src/widget.py",)
    assert repository_map.key_excerpts[0].path == "src/widget.py"
    assert "tests/test_widget.py" in repository_map.filename_matches
    assert after == before
