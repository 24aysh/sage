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
