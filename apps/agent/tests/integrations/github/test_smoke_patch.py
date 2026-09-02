import pytest

from sage.integrations.github.publication_smoke import normalize_null_file_headers


@pytest.mark.parametrize("alias", ["dev/null", "a/dev/null", "b/dev/null"])
def test_normalize_null_source_alias(alias: str) -> None:
    patch = f"--- {alias}\n+++ b/app.py\n"

    assert normalize_null_file_headers(patch) == "--- /dev/null\n+++ b/app.py\n"


@pytest.mark.parametrize("alias", ["dev/null", "a/dev/null", "b/dev/null"])
def test_normalize_null_target_alias(alias: str) -> None:
    patch = f"--- a/app.py\n+++ {alias}\n"

    assert normalize_null_file_headers(patch) == "--- a/app.py\n+++ /dev/null\n"


def test_normalize_preserves_metadata_and_line_endings() -> None:
    patch = "--- dev/null\r\n+++ b/app.py\t2026-08-27\r\n"

    assert normalize_null_file_headers(patch) == (
        "--- /dev/null\n+++ b/app.py\t2026-08-27\n"
    )


def test_normalize_preserves_real_repository_dev_null_pair() -> None:
    patch = "--- a/dev/null\n+++ b/dev/null\n"

    assert normalize_null_file_headers(patch) == patch
