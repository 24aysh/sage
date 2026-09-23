"""Instructions are bounded accepted-base snapshots, distinct for each role."""

import pytest

from sage.config import Settings
from sage.errors import WorkspaceError
from sage.harness.context.instructions import (
    MAX_INSTRUCTIONS_BYTES, RoleInstructions, read_instructions, with_repository_instructions,
)


def test_defaults_discover_independent_role_snapshots(tmp_path):
    solver = tmp_path / "sage-solver.md"
    reviewer = tmp_path / "sage-reviewer.md"
    solver.write_text("Use focused tests.")
    reviewer.write_text("Check backwards compatibility.")
    settings = Settings(openai_api_key="test")
    snapshot = RoleInstructions.load(tmp_path, settings)
    solver.write_text("Ignore tests.")
    reviewer.unlink()
    assert snapshot.solver == "Use focused tests."
    assert snapshot.reviewer == "Check backwards compatibility."
    assert RoleInstructions.load(tmp_path, settings).reviewer == ""


def test_configured_paths_and_missing_defaults(tmp_path):
    assert RoleInstructions.load(tmp_path, Settings(openai_api_key="test")) == RoleInstructions()
    directory = tmp_path / "instructions"
    directory.mkdir()
    (directory / "solver.md").write_text("Solver-specific guidance.")
    settings = Settings.from_env({"OPENAI_API_KEY": "test", "GEMINI_API_KEY": "test",
        "SAGE_SOLVER_INSTRUCTIONS_FILE": "instructions/solver.md",
        "SAGE_REVIEWER_INSTRUCTIONS_FILE": "instructions/reviewer.md"})
    assert RoleInstructions.load(tmp_path, settings).solver == "Solver-specific guidance."


@pytest.mark.parametrize("path", ["../outside.md", "/etc/passwd", "", "bad\x00name", "a\nb"])
def test_unsafe_paths_fail_before_read(tmp_path, path):
    with pytest.raises(WorkspaceError, match="repository-relative"):
        read_instructions(tmp_path, path)


def test_symlink_escape_is_rejected(tmp_path):
    (tmp_path / "solver.md").symlink_to(tmp_path.parent / "outside.md")
    with pytest.raises(WorkspaceError, match="escapes"):
        read_instructions(tmp_path, "solver.md")


@pytest.mark.parametrize("content", [b"\xff", b"x" * (MAX_INSTRUCTIONS_BYTES + 1)])
def test_invalid_or_oversized_content_fails(tmp_path, content):
    (tmp_path / "solver.md").write_bytes(content)
    with pytest.raises(WorkspaceError):
        read_instructions(tmp_path, "solver.md")


def test_directory_is_not_guidance_and_empty_file_is_allowed(tmp_path):
    with pytest.raises(WorkspaceError, match="regular UTF-8 file"):
        read_instructions(tmp_path, ".")
    (tmp_path / "solver.md").touch()
    assert read_instructions(tmp_path, "solver.md") == ""


def test_platform_precedence_and_no_duplicate_guidance():
    prompt = with_repository_instructions("Platform", "Repository")
    assert prompt.startswith("Platform")
    assert prompt.count("\nRepository\n") == 1
    assert "take precedence" in prompt
    assert with_repository_instructions("Platform", "") == "Platform"
