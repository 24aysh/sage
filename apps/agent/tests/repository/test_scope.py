import pytest

from sage.errors import RepositoryError
from sage.repository.scope import paths_outside_scopes, validate_write_scopes


def test_write_scope_accepts_exact_and_directory_patterns() -> None:
    assert paths_outside_scopes(
        ["app.py", "tests/test_app.py"],
        allowed_scopes=("app.py", "tests/**"),
    ) == ()
    assert paths_outside_scopes(
        ["app.py", "docs/readme.md"],
        allowed_scopes=("app.py",),
    ) == ("docs/readme.md",)


def test_write_scope_enforces_protected_forbidden_patterns() -> None:
    assert paths_outside_scopes(
        [".git/config", ".sage/runs/example/terminal.json"],
        allowed_scopes=("**",),
    ) == (".git/config", ".sage/runs/example/terminal.json")


@pytest.mark.parametrize("scope", ["", "/tmp/file", "../file", ".git/config"])
def test_write_scope_rejects_unsafe_values(scope: str) -> None:
    with pytest.raises(RepositoryError):
        validate_write_scopes((scope,))
