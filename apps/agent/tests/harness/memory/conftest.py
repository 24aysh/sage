from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from sage.harness.memory.parsing import CodeParser, PARSER_VERSION
from sage.harness.memory.service import LegionMemoryService
from sage.harness.memory.session import MemorySession
from sage.harness.memory.store import GraphStore


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--legion-reference", default=None,
                     help="Optional trusted pinned reference checkout for offline differential tests.")


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


def apply_files(
    store: GraphStore, files: dict[str, str], *, full: bool = True,
    removed: tuple[str, ...] = (),
) -> None:
    """Index in-memory source fixtures through the real parser and graph store."""
    parser = CodeParser()
    store.apply_update(
        parsed_files=[parser.parse_bytes(text.encode(), relative_path=path)
                      for path, text in files.items()],
        removed_files=removed, repository_id="fixture", indexed_sha="sha",
        parser_version=PARSER_VERSION,
        build_type="full" if full else "incremental", full_rebuild=full,
    )


@pytest.fixture
def memory_session(
    fixture_repo: Path, built_memory: tuple[LegionMemoryService, Path],
) -> Iterator[MemorySession]:
    service, database = built_memory
    build = service.build_or_update_graph_tool(repo_root=fixture_repo, memory_file=database)
    retrieval = service.retrieve_issue_context(
        issue_text="helper", repo_root=fixture_repo, memory_file=database)
    session = MemorySession(service, fixture_repo, database, database, build, retrieval)
    try:
        yield session
    finally:
        session.close()
