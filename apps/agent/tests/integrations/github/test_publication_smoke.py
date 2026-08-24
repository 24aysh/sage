from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from sage.errors import GitHubPublicationError
from sage.integrations.github.publication_smoke import run_publication_smoke


def test_publication_smoke_exercises_production_publisher_offline(
    tmp_path: Path,
) -> None:
    output = tmp_path / "smoke"

    result = run_publication_smoke(output, issue_number=42)

    assert result.output_dir == output
    assert result.default_branch_sha == result.base_sha
    assert result.sage_branch_sha != result.base_sha
    assert result.publication.branch_name == "sage/issue-42"
    assert result.pull_request_draft is True
    assert result.pull_request_title == "Sage: Offline publication smoke test"
    assert _git_output(
        result.remote_dir,
        "show",
        "-s",
        "--format=%s",
        result.sage_branch_sha,
    ) == "fix: resolve issue #42"
    assert _git_output(
        result.remote_dir,
        "show",
        "-s",
        "--format=%an",
        result.sage_branch_sha,
    ) == "Sage GitHub Actions"


def test_publication_smoke_applies_supplied_patch_with_whitespace_fix(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "source"
    _git(tmp_path, "init", "-b", "main", str(repository))
    _git(repository, "config", "user.name", "Test User")
    _git(repository, "config", "user.email", "test@example.invalid")
    (repository / "app.py").write_text("value = 1\n", encoding="utf-8")
    _git(repository, "add", "app.py")
    _git(repository, "commit", "-m", "base")
    base_sha = _git_output(repository, "rev-parse", "HEAD")
    patch = tmp_path / "candidate.patch"
    patch.write_text(
        """\
diff --git a/test_app.py b/test_app.py
new file mode 100644
--- dev/null
+++ b/test_app.py
@@ -0,0 +1,2 @@
+assert True
+
""",
        encoding="utf-8",
    )

    result = run_publication_smoke(
        tmp_path / "smoke",
        repository=repository,
        patch_file=patch,
        base_ref=base_sha,
    )

    assert result.default_branch_sha == base_sha
    assert _git_output(
        result.remote_dir,
        "show",
        f"{result.sage_branch_sha}:test_app.py",
    ) == "assert True"
    assert _git_output(
        result.remote_dir,
        "diff-tree",
        "--check",
        f"{result.sage_branch_sha}^",
        result.sage_branch_sha,
    ) == ""


def test_publication_smoke_refuses_existing_output_directory(tmp_path: Path) -> None:
    output = tmp_path / "existing"
    output.mkdir()

    with pytest.raises(GitHubPublicationError, match="already exists"):
        run_publication_smoke(output)


def _git(repository: Path, *arguments: str) -> None:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def _git_output(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip()
