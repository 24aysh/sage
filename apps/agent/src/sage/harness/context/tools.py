"""Deterministic repository tools and optional structural context delivery."""

from __future__ import annotations

from collections.abc import Callable
import json

from langchain_core.tools import BaseTool, tool
from langchain_core.utils.function_calling import convert_to_openai_tool

from sage.harness.context.run import RepositoryContext, SolverContext
from sage.harness.retrieval.tools import build_retrieval_tools


def build_context_tools(context: SolverContext) -> list[BaseTool]:
    """Expose source tools plus only the useful graph profile, preserving order."""
    retrieval = context.retrieval
    retrieval_tools = build_retrieval_tools(
        retrieval.service, repo_root=retrieval.repo_root, index_file=retrieval.index_file,
        output_chars=context.settings.max_tool_output_chars,
        usage_recorder=retrieval.record_tool_call, source_reader=context.repository.read_file,
        profile="solve", response_filter=retrieval.filter_response,
    ) if retrieval is not None and retrieval.tools_enabled else []
    if retrieval is not None and retrieval_tools:
        retrieval.record_schemas(len(json.dumps([convert_to_openai_tool(t) for t in retrieval_tools],
                                            separators=(",", ":"))))
    return [
        *build_repository_read_tools(context, enrich=retrieval.enrich if retrieval else None,
                                     output_chars=context.settings.max_tool_output_chars),
        *build_repository_branch_tools(context),
        *retrieval_tools,
    ]


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

        return await search(query, path, max_results)

    async def search(query: str, path: str, max_results: int) -> str:
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

    async def read(path: str, start_line: int, end_line: int | None) -> str:
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

        result = context.repository.switch_branch(branch_name=branch_name)
        if retrieval := getattr(context, "retrieval", None):
            retrieval.close()
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
