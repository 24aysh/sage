"""Shared read-only repository tool registry for V2 graph nodes."""

from __future__ import annotations

from langchain_core.tools import BaseTool, tool

from sage.domain.runtime import RuntimeContext


def build_repository_read_tools(
    context: RuntimeContext,
    *,
    include_diff: bool = False,
) -> list[BaseTool]:
    """Build one reusable bounded repository read registry."""

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

        return context.repository.search_text(
            query=query,
            path=path,
            max_results=max_results,
        )

    @tool
    async def read_file(
        path: str,
        start_line: int = 1,
        end_line: int | None = None,
    ) -> str:
        """Read at most 300 numbered lines from a repository text file."""

        return context.repository.read_file(
            path=path,
            start_line=start_line,
            end_line=end_line,
        )

    tools: list[BaseTool] = [list_tree, search_text, read_file]
    if not include_diff:
        return tools

    @tool
    async def show_diff() -> str:
        """Show actual bounded Git status, statistics, and candidate diff."""

        return context.repository.show_diff()

    return [*tools, show_diff]
