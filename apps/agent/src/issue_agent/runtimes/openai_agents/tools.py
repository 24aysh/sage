"""Thin SDK wrappers around project-owned repository operations."""

from __future__ import annotations

from typing import Any

from agents import function_tool

from issue_agent.domain.runtime import RuntimeContext


def build_tools(context: RuntimeContext) -> list[Any]:
    """Build function tools closed over one trusted runtime context."""

    @function_tool
    def list_tree(path: str = ".", max_depth: int = 2) -> str:
        """List a bounded repository tree without file contents."""

        return context.repository.list_tree(path=path, max_depth=max_depth)

    @function_tool
    def search_text(
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

    @function_tool
    def read_file(
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

    @function_tool
    def apply_patch(patch: str) -> str:
        """Apply a validated unified Git diff to the isolated repository."""

        return context.repository.apply_patch(patch=patch)

    @function_tool
    def show_diff() -> str:
        """Show the actual bounded Git status, diff statistics, and diff."""

        return context.repository.show_diff()

    @function_tool
    def run_command(command: str, timeout_seconds: int | None = None) -> str:
        """Run a repository command inside Docker and return structured output."""

        result = context.repository.run_command(
            command=command,
            timeout_seconds=timeout_seconds,
        )
        return context.repository.format_command_result(result)

    return [list_tree, search_text, read_file, apply_patch, show_diff, run_command]
