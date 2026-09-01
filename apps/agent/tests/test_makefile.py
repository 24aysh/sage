"""Behavioral checks for the repository's V2 Make targets."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def _first_run_target() -> str:
    makefile = (REPOSITORY_ROOT / "Makefile").read_text(encoding="utf-8")
    return makefile.split("first-run:", 1)[1].split("\nv2-first-run:", 1)[0]


def test_first_run_preserves_opt_in_langsmith_tracing() -> None:
    target = _first_run_target()

    assert "export LANGSMITH_TRACING=false" not in target
    assert 'LANGSMITH_TRACING:=false' in target
    assert 'LANGSMITH_PROJECT:=sage-v2' in target


def test_first_run_defaults_google_context_approval_to_true() -> None:
    target = _first_run_target()

    assert 'SAGE_GOOGLE_MODEL_CONTEXT_APPROVED:-true' in target


def test_first_run_selects_v2_without_admission_configuration() -> None:
    target = _first_run_target()

    assert "export SAGE_RUNTIME=v2" in target
    assert "ADMISSION" not in target


def test_v2_first_run_is_a_compatibility_alias() -> None:
    makefile = (REPOSITORY_ROOT / "Makefile").read_text(encoding="utf-8")

    assert "v2-first-run: first-run" in makefile


def test_run_status_disables_the_git_pager() -> None:
    makefile = (REPOSITORY_ROOT / "Makefile").read_text(encoding="utf-8")
    target = makefile.split("run-status:", 1)[1].split("\nrun-test:", 1)[0]

    assert 'git --no-pager -C "$$run_dir/repo" diff --stat' in target
    assert 'git --no-pager -C "$$run_dir/repo" diff --check' in target


def test_memory_cold_start_has_bounded_destructive_scope() -> None:
    makefile = (REPOSITORY_ROOT / "Makefile").read_text(encoding="utf-8")
    target = makefile.split("memory-cold-start:", 2)[2]

    assert 'ENV_FILE = .env.memory.local' in makefile
    assert 'find "$(ROOT_DIR)/.sage/runs" -mindepth 1 -delete' in target
    assert "docker compose -p sage-memory-test" in target
    assert "docker-compose.yml down -v --remove-orphans" in target
    assert "docker-compose.yml up -d --wait" in target
    assert 'uv run --project "$(AGENT_PROJECT)" sage memory migrate' in target
    assert "only accepts the named disposable local PostgreSQL DSNs" in target


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
