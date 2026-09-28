"""Behavioral checks for the repository's canonical Make targets."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def test_run_status_disables_the_git_pager() -> None:
    makefile = (REPOSITORY_ROOT / "Makefile").read_text(encoding="utf-8")
    target = makefile.split("run-status:", 1)[1].split("\nrun-test:", 1)[0]

    assert 'git --no-pager -C "$$run_dir/repo" diff --stat' in target
    assert 'git --no-pager -C "$$run_dir/repo" diff --check' in target


def test_retrieval_build_uses_bound_repository_and_optional_index() -> None:
    makefile = (REPOSITORY_ROOT / "Makefile").read_text(encoding="utf-8")
    target = makefile.split("retrieval-build:", 1)[1].split("\nretrieval-preview:", 1)[0]

    assert 'args=(retrieval build --repo "$(REPO)")' in target
    assert '--index-file "$(INDEX_FILE)"' in target
    assert "LANGSMITH_TRACING=false" in target
    assert "OPENAI_API_KEY" not in target


def test_retrieval_preview_requires_issue_and_explicit_index() -> None:
    makefile = (REPOSITORY_ROOT / "Makefile").read_text(encoding="utf-8")
    target = makefile.split("retrieval-preview:", 1)[1].split("\neval-retrieval:", 1)[0]

    assert "REPO, ISSUE, and INDEX are required" in target
    assert "sage retrieval retrieve" in target
    assert '--repo "$(REPO)"' in target
    assert '--issue-file "$(ISSUE)"' in target
    assert '--index-file "$(INDEX)"' in target
    assert "LANGSMITH_TRACING=false" in target
    assert "OPENAI_API_KEY" not in target


def test_deprecated_legion_make_targets_and_variables_are_absent() -> None:
    makefile = (REPOSITORY_ROOT / "Makefile").read_text(encoding="utf-8")

    for name in ("legion-memory", "legion-retrieve", "legion-solve", "clean-legion-memory"):
        assert f"{name}:" not in makefile
    assert "MEMORY_FILE ?=" not in makefile
    assert "MEMORY ?=" not in makefile


@pytest.mark.parametrize(
    ("target", "directory_name"),
    (
        ("clean-runs", "runs"),
        ("clean-retrieval", "retrieval"),
    ),
)
def test_clean_target_removes_only_contents_and_preserves_parent(
    tmp_path: Path,
    target: str,
    directory_name: str,
) -> None:
    makefile = tmp_path / "Makefile"
    makefile.write_text(
        (REPOSITORY_ROOT / "Makefile").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    sage_directory = tmp_path / ".sage"
    target_directory = sage_directory / directory_name
    nested_directory = target_directory / "nested"
    nested_directory.mkdir(parents=True)
    (target_directory / ".hidden").write_text("hidden", encoding="utf-8")
    (nested_directory / "artifact").write_text("artifact", encoding="utf-8")
    sibling = sage_directory / "unrelated"
    sibling.mkdir()
    (sibling / "keep").write_text("keep", encoding="utf-8")

    result = subprocess.run(
        ["make", "-f", str(makefile), target],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert target_directory.is_dir()
    assert list(target_directory.iterdir()) == []
    assert (sibling / "keep").read_text(encoding="utf-8") == "keep"
