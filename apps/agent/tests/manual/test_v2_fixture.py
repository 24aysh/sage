"""Checks for the checked-in V2 manual workflow fixture."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
FIXTURE_ROOT = REPOSITORY_ROOT / "v2-manual-test"


def _run_fixture_tests(project: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "calculator_checks.py"],
        cwd=project,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_manual_fixture_reproduces_and_resolves_the_documented_bug(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    shutil.copytree(FIXTURE_ROOT / "project", project)

    baseline = _run_fixture_tests(project)
    assert baseline.returncode == 1
    assert "FAILED (failures=2)" in baseline.stderr

    calculator = project / "calculator.py"
    original = calculator.read_text(encoding="utf-8")
    assert "return left - right" in original
    calculator.write_text(
        original.replace("return left - right", "return left + right"),
        encoding="utf-8",
    )

    repaired = _run_fixture_tests(project)
    assert repaired.returncode == 0, repaired.stderr
    assert "OK" in repaired.stderr


def test_manual_issue_names_the_strict_scope_and_verification() -> None:
    issue = (FIXTURE_ROOT / "issue.md").read_text(encoding="utf-8")

    assert "Only `calculator.py` is changed." in issue
    assert "python3 calculator_checks.py" in issue


def test_v2_first_run_accepts_custom_repo_and_issue_before_credentials(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    issue = tmp_path / "issue.md"
    issue.write_text("# Test issue\n", encoding="utf-8")
    environment = os.environ.copy()
    for name in (
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "ANTHROPIC_API_KEY",
        "SAGE_GOOGLE_MODEL_CONTEXT_APPROVED",
    ):
        environment.pop(name, None)

    result = subprocess.run(
        [
            "make",
            "v2-first-run",
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
    output = result.stdout + result.stderr
    assert "OPENAI_API_KEY is not configured" in output
    assert "sample project is missing" not in output
