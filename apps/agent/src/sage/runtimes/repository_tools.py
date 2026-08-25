"""Shared LangChain adapters for bounded repository reads."""

from __future__ import annotations

from langchain_core.tools import BaseTool, tool

from sage.domain.runtime import RuntimeContext


def build_repository_read_tools(context: RuntimeContext) -> list[BaseTool]:
    """Build the repository read tools shared by every agent runtime."""

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

    return [list_tree, search_text, read_file]


def build_show_diff_tool(
    context: RuntimeContext,
    *,
    description: str,
) -> BaseTool:
    """Build a diff tool while retaining each runtime's model-facing wording."""

    @tool(description=description)
    async def show_diff() -> str:
        return context.repository.show_diff()

    return show_diff
