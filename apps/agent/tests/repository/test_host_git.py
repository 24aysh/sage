import subprocess
from pathlib import Path

import pytest

from sage.errors import HostGitError, HostGitTimeoutError
from sage.repository.host_git import run_git


def test_run_git_executes_against_explicit_repository(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    initialized = run_git(["init"], repository=repository)

    result = run_git(["rev-parse", "--show-toplevel"], repository=repository)

    assert initialized.returncode == 0
    assert result.returncode == 0
    assert Path(result.stdout.strip()) == repository.resolve()


def test_run_git_rejects_invalid_timeout() -> None:
    with pytest.raises(ValueError, match="at least one second"):
        run_git(["--version"], timeout_seconds=0)


def test_run_git_maps_missing_executable(monkeypatch) -> None:
    def missing(*args, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", missing)

    with pytest.raises(HostGitError, match="executable was not found"):
        run_git(["--version"])


def test_run_git_maps_timeout_without_environment_values(monkeypatch) -> None:
    def timed_out(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], timeout=3)

    monkeypatch.setattr(subprocess, "run", timed_out)

    with pytest.raises(HostGitTimeoutError) as raised:
        run_git(
            ["fetch"],
            timeout_seconds=3,
            environment={"SAGE_GITHUB_TOKEN": "must-not-leak"},
        )

    assert "must-not-leak" not in str(raised.value)
