from pathlib import Path

from sage.memory.git_state import GitStateReader
from sage.repository.host_git import run_git


def _git(repository: Path, arguments: list[str]) -> str:
    result = run_git(arguments, repository=repository)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_git_state_reads_committed_blob_not_candidate_content(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    _git(repository, ["init"])
    _git(repository, ["config", "user.name", "Test"])
    _git(repository, ["config", "user.email", "test@example.com"])
    source = repository / "engine.py"
    source.write_text("VALUE = 'base'\n", encoding="utf-8")
    _git(repository, ["add", "engine.py"])
    _git(repository, ["commit", "-m", "base"])
    commit = _git(repository, ["rev-parse", "HEAD"])
    source.write_text("VALUE = 'candidate'\n", encoding="utf-8")

    oid, body = GitStateReader(repository).read_blob(commit, "engine.py")

    assert len(oid) == 40
    assert body == "VALUE = 'base'\n"
    assert GitStateReader(repository).list_files(commit) == [("engine.py", oid)]
