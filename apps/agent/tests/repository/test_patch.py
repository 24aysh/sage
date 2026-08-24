import logging
import shlex
import subprocess
from pathlib import Path

import pytest

from sage.errors import PatchError
from sage.repository.patch import apply_patch
from sage.sandbox.base import CommandResult


class SuccessfulSandbox:
    def start(self) -> None:
        pass

    def exec(
        self,
        command: str,
        *,
        timeout_seconds: int | None = None,
    ) -> CommandResult:
        return CommandResult(command=command, exit_code=0, stdout="", stderr="")

    def stop(self) -> None:
        pass


class LocalPatchSandbox:
    """Execute the generated Git command against a temporary repository."""

    def __init__(self, repository: Path) -> None:
        self.repository = repository
        self.commands: list[str] = []

    def exec(
        self,
        command: str,
        *,
        timeout_seconds: int | None = None,
    ) -> CommandResult:
        self.commands.append(command)
        arguments = shlex.split(command)
        container_patch = Path(arguments[-1])
        host_patch = self.repository / container_patch.relative_to("/workspace")
        completed = subprocess.run(
            [*arguments[:-1], str(host_patch)],
            cwd=self.repository,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        return CommandResult(
            command=command,
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )


def test_apply_patch_rejects_workspace_escape(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    patch = """\
diff --git a/../../outside.txt b/../../outside.txt
--- a/../../outside.txt
+++ b/../../outside.txt
@@ -0,0 +1 @@
+unsafe
"""

    with pytest.raises(PatchError, match="outside the workspace"):
        apply_patch(
            tmp_path,
            SuccessfulSandbox(),
            patch=patch,
            max_output_chars=1_000,
            timeout_seconds=10,
        )


def test_apply_patch_removes_temporary_patch_file(tmp_path: Path) -> None:
    git_directory = tmp_path / ".git"
    git_directory.mkdir()
    patch = """\
diff --git a/example.txt b/example.txt
--- a/example.txt
+++ b/example.txt
@@ -0,0 +1 @@
+safe
"""

    result = apply_patch(
        tmp_path,
        SuccessfulSandbox(),
        patch=patch,
        max_output_chars=1_000,
        timeout_seconds=10,
    )

    assert result == "Patch applied successfully."
    assert list(git_directory.iterdir()) == []


def test_apply_patch_recounts_inaccurate_model_hunk_lengths(
    tmp_path: Path,
    caplog,
) -> None:
    caplog.set_level(logging.INFO)
    _initialize_repository(tmp_path)
    target = tmp_path / "app.py"
    target.write_text("value = 1\n", encoding="utf-8")
    sandbox = LocalPatchSandbox(tmp_path)
    patch = """\
diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,99 +1,99 @@
-value = 1
+value = 2
"""

    result = apply_patch(
        tmp_path,
        sandbox,
        patch=patch,
        max_output_chars=1_000,
        timeout_seconds=10,
    )

    assert result == "Patch applied successfully."
    assert target.read_text(encoding="utf-8") == "value = 2\n"
    assert "--recount" in sandbox.commands[0]
    assert "Patch: applying files=1" in caplog.text
    assert "recount=true" in caplog.text
    assert "Patch: finished status=applied" in caplog.text
    assert "value = 2" not in caplog.text


def test_apply_patch_canonicalizes_bare_dev_null_for_new_file(tmp_path: Path) -> None:
    _initialize_repository(tmp_path)
    sandbox = LocalPatchSandbox(tmp_path)
    patch = """\
diff --git a/new_file.py b/new_file.py
new file mode 100644
--- dev/null
+++ b/new_file.py
@@ -0,0 +1 @@
+value = 1
"""

    apply_patch(
        tmp_path,
        sandbox,
        patch=patch,
        max_output_chars=1_000,
        timeout_seconds=10,
    )

    assert (tmp_path / "new_file.py").read_text(encoding="utf-8") == "value = 1\n"


@pytest.mark.parametrize("null_header", ["a/dev/null", '"a/dev/null"'])
def test_apply_patch_canonicalizes_prefixed_dev_null_for_new_file(
    tmp_path: Path,
    null_header: str,
    caplog,
) -> None:
    caplog.set_level(logging.INFO)
    _initialize_repository(tmp_path)
    sandbox = LocalPatchSandbox(tmp_path)
    patch = f"""\
diff --git a/new_file.py b/new_file.py
new file mode 100644
--- {null_header}
+++ b/new_file.py
@@ -0,0 +1 @@
+value = 1
"""

    apply_patch(
        tmp_path,
        sandbox,
        patch=patch,
        max_output_chars=1_000,
        timeout_seconds=10,
    )

    assert (tmp_path / "new_file.py").read_text(encoding="utf-8") == "value = 1\n"
    assert "Patch: normalized null file headers count=1" in caplog.text


def test_apply_patch_canonicalizes_prefixed_dev_null_for_deleted_file(
    tmp_path: Path,
) -> None:
    _initialize_repository(tmp_path)
    target = tmp_path / "old_file.py"
    target.write_text("value = 1\n", encoding="utf-8")
    _commit_paths(tmp_path, "old_file.py")
    sandbox = LocalPatchSandbox(tmp_path)
    patch = """\
diff --git a/old_file.py b/old_file.py
deleted file mode 100644
--- a/old_file.py
+++ b/dev/null
@@ -1 +0,0 @@
-value = 1
"""

    apply_patch(
        tmp_path,
        sandbox,
        patch=patch,
        max_output_chars=1_000,
        timeout_seconds=10,
    )

    assert not target.exists()


def test_apply_patch_preserves_real_repository_path_named_dev_null(
    tmp_path: Path,
) -> None:
    _initialize_repository(tmp_path)
    target = tmp_path / "dev" / "null"
    target.parent.mkdir()
    target.write_text("value = 1\n", encoding="utf-8")
    _commit_paths(tmp_path, "dev/null")
    sandbox = LocalPatchSandbox(tmp_path)
    patch = """\
diff --git a/dev/null b/dev/null
--- a/dev/null
+++ b/dev/null
@@ -1 +1 @@
-value = 1
+value = 2
"""

    apply_patch(
        tmp_path,
        sandbox,
        patch=patch,
        max_output_chars=1_000,
        timeout_seconds=10,
    )

    assert target.read_text(encoding="utf-8") == "value = 2\n"


def test_apply_patch_fixes_blank_line_at_end_of_file(
    tmp_path: Path,
    caplog,
) -> None:
    caplog.set_level(logging.INFO)
    _initialize_repository(tmp_path)
    _commit_paths(tmp_path)
    sandbox = LocalPatchSandbox(tmp_path)
    patch = """\
diff --git a/test_example.py b/test_example.py
new file mode 100644
--- /dev/null
+++ b/test_example.py
@@ -0,0 +1,2 @@
+assert 1 + 1 == 2
+
"""

    apply_patch(
        tmp_path,
        sandbox,
        patch=patch,
        max_output_chars=1_000,
        timeout_seconds=10,
    )

    assert (tmp_path / "test_example.py").read_text(encoding="utf-8") == (
        "assert 1 + 1 == 2\n"
    )
    assert "--whitespace=fix" in sandbox.commands[0]
    assert "Patch: whitespace normalized" in caplog.text
    diff_check = subprocess.run(
        ["git", "diff", "--check", "HEAD", "--"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )
    assert diff_check.returncode == 0


def _initialize_repository(repository: Path) -> None:
    subprocess.run(
        ["git", "init", "--quiet", str(repository)],
        check=True,
        capture_output=True,
        text=True,
    )


def _commit_paths(repository: Path, *paths: str) -> None:
    if paths:
        subprocess.run(
            ["git", "add", "--", *paths],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
        )
    empty_arguments = ["--allow-empty"] if not paths else []
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Sage Tests",
            "-c",
            "user.email=sage-tests@example.invalid",
            "commit",
            "--quiet",
            *empty_arguments,
            "-m",
            "base",
        ],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
