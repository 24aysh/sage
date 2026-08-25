"""Thin LangChain adapters around project-owned repository operations."""

from __future__ import annotations

from langchain_core.tools import BaseTool, tool

from sage.domain.runtime import RuntimeContext
from sage.runtimes.repository_tools import (
    build_repository_read_tools,
    build_show_diff_tool,
)


def build_tools(context: RuntimeContext) -> list[BaseTool]:
    """Build the six repository tools for one trusted runtime context."""

    @tool
    async def apply_patch(patch: str) -> str:
        """Apply a validated unified Git diff to the isolated repository."""

        return context.repository.apply_patch(patch=patch)

    @tool
    async def run_command(command: str, timeout_seconds: int | None = None) -> str:
        """Run a repository command inside Docker and return structured output."""

        result = context.repository.run_command(
            command=command,
            timeout_seconds=timeout_seconds,
        )
        return context.repository.format_command_result(result)

    read_tools = build_repository_read_tools(context)
    show_diff = build_show_diff_tool(
        context,
        description="Show the actual bounded Git status, diff statistics, and diff.",
    )
    return [*read_tools, apply_patch, show_diff, run_command]
