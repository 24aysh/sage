from pathlib import Path

import pytest

from sage.errors import PathSafetyError
from sage.repository.paths import resolve_workspace_path


def test_resolve_workspace_path_accepts_repository_relative_path(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()

    assert resolve_workspace_path(tmp_path, "src/module.py") == source / "module.py"


@pytest.mark.parametrize("requested", ["/etc/passwd", "../../outside", ".git/config"])
def test_resolve_workspace_path_rejects_unsafe_path(
    tmp_path: Path,
    requested: str,
) -> None:
    with pytest.raises(PathSafetyError):
        resolve_workspace_path(tmp_path, requested)


def test_resolve_workspace_path_rejects_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside"
    outside.mkdir(exist_ok=True)
    (tmp_path / "escape").symlink_to(outside, target_is_directory=True)

    with pytest.raises(PathSafetyError):
        resolve_workspace_path(tmp_path, "escape/secret.txt")


def test_resolve_workspace_path_rejects_symlink_to_git_directory(tmp_path: Path) -> None:
    git_directory = tmp_path / ".git"
    git_directory.mkdir()
    (tmp_path / "metadata").symlink_to(git_directory, target_is_directory=True)

    with pytest.raises(PathSafetyError, match="Git internals"):
        resolve_workspace_path(tmp_path, "metadata/config")
