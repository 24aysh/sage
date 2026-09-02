from pathlib import Path

import pytest

from sage.errors import PathSafetyError, RepositoryError
from sage.repository.filesystem import (
    WriteMode,
    delete_file,
    move_file,
    read_file,
    replace_text,
    resolve_workspace_path,
    write_file,
)


def test_replace_text_requires_exact_occurrence_count(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("value = 1\nvalue = 1\n", encoding="utf-8")

    with pytest.raises(RepositoryError, match="found 2"):
        replace_text(
            tmp_path,
            path="app.py",
            old_text="value = 1",
            new_text="value = 2",
        )

    assert target.read_text(encoding="utf-8") == "value = 1\nvalue = 1\n"


def test_structured_edits_cover_create_replace_move_and_delete(tmp_path: Path) -> None:
    write_file(
        tmp_path,
        path="src/app.py",
        content="value = 1\n",
        mode=WriteMode.CREATE,
    )
    replace_text(
        tmp_path,
        path="src/app.py",
        old_text="value = 1",
        new_text="value = 2",
    )
    move_file(
        tmp_path,
        source_path="src/app.py",
        destination_path="app.py",
    )
    delete_file(tmp_path, path="app.py")

    assert not (tmp_path / "app.py").exists()
    assert not (tmp_path / "src/app.py").exists()


def test_write_modes_prevent_accidental_overwrite(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("original\n", encoding="utf-8")

    with pytest.raises(RepositoryError, match="already exists"):
        write_file(
            tmp_path,
            path="app.py",
            content="changed\n",
            mode=WriteMode.CREATE,
        )
    with pytest.raises(RepositoryError, match="does not exist"):
        write_file(
            tmp_path,
            path="missing.py",
            content="changed\n",
            mode=WriteMode.REPLACE,
        )

    assert target.read_text(encoding="utf-8") == "original\n"


def test_new_files_are_readable_by_the_sandbox_user(tmp_path: Path) -> None:
    write_file(
        tmp_path,
        path="new.py",
        content="value = 1\n",
        mode=WriteMode.CREATE,
    )

    assert (tmp_path / "new.py").stat().st_mode & 0o777 == 0o644


@pytest.mark.parametrize("path", ["../outside.py", "/tmp/outside.py", ".git/config"])
def test_edits_reject_unsafe_paths(tmp_path: Path, path: str) -> None:
    with pytest.raises(PathSafetyError):
        write_file(
            tmp_path,
            path=path,
            content="unsafe",
            mode=WriteMode.CREATE_OR_REPLACE,
        )


def test_edits_reject_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-edit-target"
    outside.mkdir(exist_ok=True)
    (tmp_path / "escape").symlink_to(outside, target_is_directory=True)

    with pytest.raises(PathSafetyError):
        write_file(
            tmp_path,
            path="escape/file.txt",
            content="unsafe",
            mode=WriteMode.CREATE,
        )

    assert not (outside / "file.txt").exists()


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


def test_read_file_returns_requested_numbered_region(tmp_path: Path) -> None:
    (tmp_path / "example.py").write_text("one\ntwo\nthree\n", encoding="utf-8")

    result = read_file(
        tmp_path,
        path="example.py",
        start_line=2,
        end_line=3,
        max_output_chars=1_000,
    )

    assert result == "2 | two\n3 | three"


def test_read_file_rejects_binary_content(tmp_path: Path) -> None:
    (tmp_path / "image.bin").write_bytes(b"text\x00binary")

    with pytest.raises(RepositoryError, match="Binary"):
        read_file(tmp_path, path="image.bin", max_output_chars=1_000)


def test_read_file_enforces_line_limit(tmp_path: Path) -> None:
    (tmp_path / "large.txt").write_text("line\n" * 301, encoding="utf-8")

    with pytest.raises(RepositoryError, match="maximum of 300"):
        read_file(
            tmp_path,
            path="large.txt",
            start_line=1,
            end_line=301,
            max_output_chars=1_000,
        )
