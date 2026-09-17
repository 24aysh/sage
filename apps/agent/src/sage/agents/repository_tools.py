"""Shared LangChain adapters for bounded repository reads."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol

from langchain_core.tools import BaseTool, tool

if TYPE_CHECKING:
    from sage.repository.service import Repository


class RepositoryContext(Protocol):
    """Narrow repository capability required by agent tools."""

    repository: Repository


def build_repository_read_tools(
    context: RepositoryContext, *, enrich: Callable[..., str] | None = None,
    output_chars: int = 12_000,
) -> list[BaseTool]:
    """Build the repository read tools shared by agent roles."""

    @tool
    async def list_tree(path: str = ".", max_depth: int = 2) -> str:
        """List a bounded repository tree without file contents."""

        return context.repository.list_tree(path=path, max_depth=max_depth)

    @tool
    async def search_text(
        query: str,
        path: str = ".",
        max_results: int = 50,
    ) -> str:
        """Search repository files for an exact literal text value."""

        result = context.repository.search_text(
            query=query,
            path=path,
            max_results=max_results,
        )
        if enrich is not None:
            result += enrich(tool_name="search_text", query=query,
                             source_chars=len(result),
                             available_chars=max(0, output_chars - len(result)))
        return result

    @tool
    async def read_file(
        path: str,
        start_line: int = 1,
        end_line: int | None = None,
    ) -> str:
        """Read at most 300 numbered lines from a repository text file."""

        result = context.repository.read_file(
            path=path,
            start_line=start_line,
            end_line=end_line,
        )
        if enrich is not None:
            result += enrich(tool_name="read_file", path=path, start_line=start_line,
                             source_chars=len(result),
                             end_line=min(end_line or start_line + 299, start_line + 299),
                             available_chars=max(0, output_chars - len(result)))
        return result

    return [list_tree, search_text, read_file]


def build_repository_branch_tools(context: RepositoryContext) -> list[BaseTool]:
    """Build structured Git branch inspection and switching tools."""

    @tool
    async def list_branches() -> str:
        """List local and remote-tracking Git branches in the sandbox."""

        return context.repository.list_branches()

    @tool
    async def switch_branch(branch_name: str) -> str:
        """Switch the clean sandbox worktree to an existing Git branch."""

        return context.repository.switch_branch(branch_name=branch_name)

    return [list_branches, switch_branch]


def build_show_diff_tool(
    context: RepositoryContext,
    *,
    description: str,
) -> BaseTool:
    """Build a role-specific diff tool."""

    @tool(description=description)
    async def show_diff() -> str:
        return context.repository.show_diff()

    return show_diff
