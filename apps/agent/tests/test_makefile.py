"""Behavioral checks for the repository's canonical Make targets."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def _first_run_target() -> str:
    makefile = (REPOSITORY_ROOT / "Makefile").read_text(encoding="utf-8")
    return makefile.split("first-run:", 1)[1].split("\ngithub-smoke:", 1)[0]


def test_first_run_preserves_opt_in_langsmith_tracing() -> None:
    target = _first_run_target()

    assert "export LANGSMITH_TRACING=false" not in target
    assert 'LANGSMITH_TRACING:=false' in target
    assert 'LANGSMITH_PROJECT:=sage-v2' in target


def test_first_run_defaults_google_context_approval_to_true() -> None:
    target = _first_run_target()

    assert 'SAGE_GOOGLE_MODEL_CONTEXT_APPROVED:-true' in target


def test_run_status_disables_the_git_pager() -> None:
    makefile = (REPOSITORY_ROOT / "Makefile").read_text(encoding="utf-8")
    target = makefile.split("run-status:", 1)[1].split("\nrun-test:", 1)[0]

    assert 'git --no-pager -C "$$run_dir/repo" diff --stat' in target
    assert 'git --no-pager -C "$$run_dir/repo" diff --check' in target


def test_legion_memory_target_uses_bound_repository_and_optional_database() -> None:
    makefile = (REPOSITORY_ROOT / "Makefile").read_text(encoding="utf-8")
    target = makefile.split("legion-memory:", 1)[1].split("\nnew-issue:", 1)[0]

    assert 'args=(memory build --repo "$(REPO)")' in target
    assert '--memory-file "$(MEMORY_FILE)"' in target
    assert "LANGSMITH_TRACING=false" in target
    assert "OPENAI_API_KEY" not in target


def test_legion_retrieve_target_requires_issue_and_explicit_database() -> None:
    makefile = (REPOSITORY_ROOT / "Makefile").read_text(encoding="utf-8")
    target = makefile.split("legion-retrieve:", 1)[1].split("\nnew-issue:", 1)[0]

    assert "REPO, ISSUE, and MEMORY are required" in target
    assert "sage memory retrieve" in target
    assert '--repo "$(REPO)"' in target
    assert '--issue-file "$(ISSUE)"' in target
    assert '--memory-file "$(MEMORY)"' in target
    assert "LANGSMITH_TRACING=false" in target
    assert "OPENAI_API_KEY" not in target


def test_legion_solve_reuses_baseline_solve_with_explicit_memory() -> None:
    makefile = (REPOSITORY_ROOT / "Makefile").read_text(encoding="utf-8")
    solve_target = makefile.split("solve:", 1)[1].split("\nsolve-debug:", 1)[0]
    legion_target = makefile.split("legion-solve:", 1)[1].split(
        "\nrun-status:", 1
    )[0]

    assert "LEGION_SOLVE ?= false" in makefile
    assert 'if [[ "$(LEGION_SOLVE)" == "true" ]]' in solve_target
    assert 'memory_args=(--memory-file "$(MEMORY)")' in solve_target
    assert '"$${memory_args[@]}"' in solve_target
    assert "LEGION_SOLVE := true" in makefile
    assert "solve ## Run a live solve with Legion Memory enabled." in legion_target


def test_solve_baseline_forces_jev_off_and_omits_memory(tmp_path: Path) -> None:
    makefile = tmp_path / "Makefile"
    makefile.write_text((REPOSITORY_ROOT / "Makefile").read_text(encoding="utf-8"), encoding="utf-8")
    env_file = tmp_path / "baseline.env"
    env_file.write_text("OPENAI_API_KEY=test\nSAGE_JEV_NAVIGATION_MODE=on\n", encoding="utf-8")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    uv = binaries / "uv"
    uv.write_text("#!/bin/sh\nprintf 'jev=%s\\n' \"$SAGE_JEV_NAVIGATION_MODE\"\nprintf 'args=%s\\n' \"$*\"\n",
                  encoding="utf-8")
    uv.chmod(0o755)

    result = subprocess.run(["make", "solve-baseline", f"ENV_FILE={env_file}",
        f"REPO={tmp_path / 'repo'}", f"ISSUE={tmp_path / 'issue.md'}",
        f"MEMORY={tmp_path / 'graph.sqlite3'}", "LEGION_SOLVE=true", "BASELINE_SOLVE=false"],
        cwd=tmp_path, env={**os.environ, "PATH": f"{binaries}{os.pathsep}{os.environ['PATH']}"},
        text=True, capture_output=True, check=False)

    assert result.returncode == 0, result.stderr
    assert "jev=off" in result.stdout
    assert "--memory-file" not in result.stdout


@pytest.mark.parametrize(
    ("target", "directory_name"),
    (
        ("clean-runs", "runs"),
        ("clean-legion-memory", "legion-memory"),
        ("clean-embeddings", "embeddings"),
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


def test_first_run_validates_inputs_before_credentials(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    issue = tmp_path / "issue.md"
    issue.write_text("# Test issue\n", encoding="utf-8")
    environment = os.environ.copy()
    for name in (
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "SAGE_GOOGLE_MODEL_CONTEXT_APPROVED",
    ):
        environment.pop(name, None)

    result = subprocess.run(
        [
            "make",
            "first-run",
            f"REPO={repository}",
            f"ISSUE={issue}",
            "ENV_FILE=/dev/null",
        ],
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env=environment,
    )

    assert result.returncode != 0
    assert "OPENAI_API_KEY is not configured" in result.stdout + result.stderr
