from pathlib import Path

import pytest

from issue_agent.errors import RepositoryError
from issue_agent.repository.files import read_file


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
        read_file(
            tmp_path,
            path="image.bin",
            max_output_chars=1_000,
        )


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
