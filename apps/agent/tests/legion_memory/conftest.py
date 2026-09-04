from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from sage.legion_memory.service import LegionMemoryService


def git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def commit_all(repository: Path, message: str) -> str:
    git(repository, "add", "--all")
    git(repository, "commit", "-m", message)
    return git(repository, "rev-parse", "HEAD")


@pytest.fixture
def fixture_repo(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    git(repository, "init", "--initial-branch=main")
    git(repository, "config", "user.name", "Sage Tests")
    git(repository, "config", "user.email", "sage-tests@example.invalid")
    (repository / "app.py").write_text(
        "from service import Worker\n\n"
        "def main():\n"
        "    return Worker().run()\n",
        encoding="utf-8",
    )
    (repository / "service.py").write_text(
        "class Base:\n"
        "    pass\n\n"
        "class Worker(Base):\n"
        "    def run(self):\n"
        "        return helper()\n\n"
        "def helper():\n"
        "    return 42\n",
        encoding="utf-8",
    )
    tests = repository / "tests"
    tests.mkdir()
    (tests / "test_service.py").write_text(
        "from service import helper\n\n"
        "def test_helper():\n"
        "    assert helper() == 42\n",
        encoding="utf-8",
    )
    (repository / "web.ts").write_text(
        "export function render(): string { return 'ready'; }\n",
        encoding="utf-8",
    )
    commit_all(repository, "initial fixture")
    return repository


@pytest.fixture
def built_memory(
    fixture_repo: Path,
    tmp_path: Path,
) -> tuple[LegionMemoryService, Path]:
    service = LegionMemoryService(data_root=tmp_path / "memory")
    result = service.build_or_update_graph_tool(repo_root=fixture_repo)
    return service, result.memory_file
