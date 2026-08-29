"""Shared LangChain adapters for bounded repository reads."""

from __future__ import annotations

import logging

from langchain_core.tools import BaseTool, tool

from sage.artifacts.v2 import V2ArtifactStore
from sage.domain.runtime import RuntimeContext
from sage.memory.models import (
    ContextExpansionRequest,
    DirectMaterializationRequest,
    MemoryMode,
)

logger = logging.getLogger(__name__)


def build_repository_read_tools(context: RuntimeContext) -> list[BaseTool]:
    """Build the repository read tools shared by every agent runtime."""

    @tool
    async def list_tree(path: str = ".", max_depth: int = 2) -> str:
        """List a bounded repository tree without file contents."""

        if context.memory_session is not None:
            return await context.memory_session.list_tree(
                path=path, max_depth=max_depth
            )
        return context.repository.list_tree(path=path, max_depth=max_depth)

    @tool
    async def search_text(
        query: str,
        path: str = ".",
        max_results: int = 50,
    ) -> str:
        """Search repository files for an exact literal text value."""

        if context.memory_session is not None:
            return await context.memory_session.search_text(
                query=query, path=path, max_results=max_results
            )
        return context.repository.search_text(
            query=query, path=path, max_results=max_results
        )

    @tool
    async def read_file(
        path: str,
        start_line: int = 1,
        end_line: int | None = None,
    ) -> str:
        """Read at most 300 numbered lines from a repository text file."""

        if context.memory_session is not None:
            return await context.memory_session.read_file(
                path=path, start_line=start_line, end_line=end_line
            )
        return context.repository.read_file(
            path=path, start_line=start_line, end_line=end_line
        )

    tools: list[BaseTool] = [list_tree, search_text, read_file]
    if (
        context.memory_session is not None
        and context.memory_session.mode is not MemoryMode.DISABLED
    ):
        expansion_sequence = 0

        @tool
        async def expand_context(query: str, reason: str) -> str:
            """Expand healthy SMRT context for one bounded semantic question."""

            nonlocal expansion_sequence
            forest = await context.memory_session.expand(
                ContextExpansionRequest(query=query, reason=reason)
            )
            if context.memory_session.mode is MemoryMode.FALLBACK:
                return "Memory entered fallback; use ordinary repository exploration."
            logger.debug(
                "memory expansion supplied to agent files=%d coverage=%r",
                len(forest.entries),
                [
                    {
                        "path": entry.path,
                        "lines": entry.included_line_ranges,
                        "chars": len(entry.source or ""),
                    }
                    for entry in forest.entries
                ],
            )
            expansion_sequence += 1
            V2ArtifactStore(
                context.prepared_run.run_dir
            ).write_context_expansion(expansion_sequence, forest)
            return forest.model_dump_json(indent=2)

        @tool
        async def materialize_dependency(path: str, reason: str) -> str:
            """Read one concrete dependency and add it to active context."""

            return await context.memory_session.materialize_dependency(
                DirectMaterializationRequest(path=path, reason=reason)
            )

        @tool
        async def inspect_context() -> str:
            """Show active paths, provenance, and current read coverage."""

            return context.memory_session.inspect_context()

        tools.extend([expand_context, materialize_dependency, inspect_context])
    return tools


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
