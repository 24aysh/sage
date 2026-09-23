"""Repository tool binding and shared graph/Jev evidence delivery."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated
import json

from langchain_core.tools import BaseTool, tool
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import Field

from sage.harness.context.run import RepositoryContext, SolverContext
from sage.harness.memory.tools import build_legion_memory_tools


def build_context_tools(context: SolverContext) -> list[BaseTool]:
    """Expose source tools plus only the useful graph profile, preserving order."""
    memory = context.memory
    memory_tools = build_legion_memory_tools(
        memory.service, repo_root=memory.repo_root, memory_file=memory.memory_file,
        output_chars=context.settings.max_tool_output_chars,
        usage_recorder=memory.record_tool_call, source_reader=context.repository.read_file,
        profile="solve", response_filter=memory.filter_response,
    ) if memory is not None and memory.tools_enabled else []
    if memory is not None and memory_tools:
        memory.record_schemas(len(json.dumps([convert_to_openai_tool(t) for t in memory_tools],
                                            separators=(",", ":"))))
    return [
        *build_repository_read_tools(context, enrich=memory.enrich if memory else None,
                                     output_chars=context.settings.max_tool_output_chars),
        *build_repository_branch_tools(context),
        *memory_tools,
    ]


def build_repository_read_tools(
    context: RepositoryContext, *, enrich: Callable[..., str] | None = None,
    output_chars: int = 12_000,
) -> list[BaseTool]:
    """Build the repository read tools shared by agent roles."""
    navigation = getattr(context, "navigation", None)

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

        return await search(query, path, max_results)

    async def search(query: str, path: str, max_results: int, goal: str | None = None) -> str:
        if navigation is not None:
            result = context.repository.search_matches(query=query, path=path, max_results=max_results)
            return result.text + await navigation.enrich(tool_name="search_text", source=result.text,
                matches=result.matches, query=query, path=path, exploration_goal=goal)
        result = context.repository.search_text(query=query, path=path, max_results=max_results)
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

        return await read(path, start_line, end_line)

    async def read(path: str, start_line: int, end_line: int | None, goal: str | None = None) -> str:
        result = context.repository.read_file(
            path=path,
            start_line=start_line,
            end_line=end_line,
        )
        if navigation is not None:
            return result + await navigation.enrich(tool_name="read_file", source=result, path=path,
                start_line=start_line, exploration_goal=goal)
        if enrich is not None:
            result += enrich(tool_name="read_file", path=path, start_line=start_line,
                             source_chars=len(result),
                             end_line=min(end_line or start_line + 299, start_line + 299),
                             available_chars=max(0, output_chars - len(result)))
        return result

    if navigation is not None and navigation.action_policy:
        @tool("search_text")
        async def search_with_goal(query: str, path: str = ".", max_results: int = 50,
                                   exploration_goal: Annotated[str, Field(min_length=1, max_length=600)] | None = None) -> str:
            """Search literal text; optionally supply a <=600-character read-only exploration goal."""
            return await search(query, path, max_results, exploration_goal)

        @tool("read_file")
        async def read_with_goal(path: str, start_line: int = 1, end_line: int | None = None,
                                 exploration_goal: Annotated[str, Field(min_length=1, max_length=600)] | None = None) -> str:
            """Read numbered source; optionally supply a <=600-character read-only exploration goal."""
            return await read(path, start_line, end_line, exploration_goal)

        return [list_tree, search_with_goal, read_with_goal]
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

        result = context.repository.switch_branch(branch_name=branch_name)
        if navigation := getattr(context, "navigation", None):
            navigation.invalidate()
        return result

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
