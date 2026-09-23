"""Immutable repository guidance for each role for the lifetime of a solve."""

from dataclasses import dataclass
import logging
from pathlib import Path

from sage.config import Settings
from sage.errors import WorkspaceError

logger = logging.getLogger(__name__)
MAX_INSTRUCTIONS_BYTES = 12_000

NAVIGATION_INSTRUCTIONS = """
Optional read-only navigation: search_text and read_file accept exploration_goal.
When useful, supply one concrete objective in at most 600 characters. It expires
after this tool response. Sage may append up to two attributed read-only observations
within the same response. Reuse useful supplied evidence before requesting it again.
Navigation cannot edit, verify, approve, or complete work; you retain those decisions.
Omit the goal when the requested observation alone is sufficient.
"""


@dataclass(frozen=True)
class RoleInstructions:
    solver: str = ""
    reviewer: str = ""

    @classmethod
    def load(cls, workspace: Path, settings: Settings) -> RoleInstructions:
        return cls(
            solver=read_instructions(workspace, settings.solver_instructions_file),
            reviewer=read_instructions(workspace, settings.reviewer_instructions_file),
        )


def read_instructions(workspace: Path, configured_path: str) -> str:
    """Read a bounded file from the accepted checkout; absent guidance is allowed."""
    path = Path(configured_path)
    if not configured_path.strip() or path.is_absolute() or ".." in path.parts or any(
        c in configured_path for c in "\x00\r\n"
    ):
        raise WorkspaceError("Role instruction paths must be nonempty repository-relative paths.")
    try:
        root = workspace.resolve()
        target = (root / path).resolve()
        if not target.is_relative_to(root):
            raise WorkspaceError("Role instruction path escapes the accepted repository.")
        if not target.exists():
            logger.info("Role instructions: absent path=%s", configured_path)
            return ""
        if not target.is_file():
            raise WorkspaceError(f"Role instructions must be a regular UTF-8 file: {configured_path}")
        with target.open("rb") as stream:
            content = stream.read(MAX_INSTRUCTIONS_BYTES + 1)
        if len(content) > MAX_INSTRUCTIONS_BYTES:
            raise WorkspaceError(f"Role instructions exceed {MAX_INSTRUCTIONS_BYTES} bytes: {configured_path}")
        text = content.decode("utf-8").strip()
    except (OSError, UnicodeError, ValueError) as error:
        raise WorkspaceError(f"Unable to read role instructions: {configured_path!r}") from error
    logger.info("Role instructions: loaded path=%s bytes=%d", configured_path, len(content))
    return text


def with_repository_instructions(base: str, guidance: str) -> str:
    if not guidance:
        return base
    return (f"{base}\nApply the following repository guidance throughout this role. "
            "Sage's role, Issue scope, safety, plan gate, verification, review, and output "
            "contracts take precedence.\n<repository-instructions>\n"
            f"{guidance}\n</repository-instructions>")
