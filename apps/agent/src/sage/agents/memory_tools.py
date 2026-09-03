"""Native read-only agent adapters for a validated Legion Memory snapshot."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from langchain_core.tools import BaseTool, tool

from sage.errors import LegionMemoryQueryError
from sage.legion_memory.service import LegionMemoryService

DEFAULT_MEMORY_TOOL_OUTPUT_CHARS = 12_000


def build_legion_memory_tools(
    service: LegionMemoryService,
    *,
    repo_root: Path,
    memory_file: Path,
    output_chars: int = DEFAULT_MEMORY_TOOL_OUTPUT_CHARS,
) -> list[BaseTool]:
    """Bind read-only graph tools to one repository and immutable graph path."""

    if output_chars < 1_000:
        raise ValueError("Legion Memory tool output_chars must be at least 1000.")

    def invoke(
        operation: Callable[..., dict[str, object]],
        **arguments: object,
    ) -> str:
        try:
            result = operation(
                repo_root=repo_root,
                memory_file=memory_file,
                **arguments,
            )
        except LegionMemoryQueryError as error:
            result = {
                "status": "unavailable",
                "summary": str(error)[:500],
                "repository_id": None,
                "indexed_sha": None,
                "last_updated": None,
                "total": 0,
                "returned": 0,
                "omitted": 0,
                "truncated": False,
                "data": {},
            }
        return _bounded_json(result, max_chars=output_chars)

    @tool
    async def list_graph_stats_tool() -> str:
        """Report graph readiness, provenance, scope, and aggregate counts."""

        return invoke(service.list_graph_stats_tool)

    @tool
    async def get_minimal_context_tool(task: str) -> str:
        """Return a compact graph starting map for the current task."""

        return invoke(service.get_minimal_context_tool, task=task)

    @tool
    async def semantic_search_nodes_tool(
        query: str,
        kind: Literal["File", "Class", "Type", "Function", "Test"] | None = None,
        limit: int = 20,
    ) -> str:
        """Find graph nodes by identifier, path, signature, or task terms."""

        return invoke(
            service.semantic_search_nodes_tool,
            query=query,
            kind=kind,
            limit=limit,
        )

    @tool
    async def query_graph_tool(
        pattern: Literal[
            "callers_of",
            "callees_of",
            "references_to",
            "imports_of",
            "importers_of",
            "children_of",
            "tests_for",
            "inheritors_of",
            "file_summary",
        ],
        target: str,
        max_results: int = 50,
    ) -> str:
        """Query a supported relationship or a repository-relative file summary."""

        return invoke(
            service.query_graph_tool,
            pattern=pattern,
            target=target,
            max_results=max_results,
        )

    @tool
    async def traverse_graph_tool(
        target: str,
        direction: Literal["incoming", "outgoing", "both"] = "both",
        max_depth: int = 2,
        max_results: int = 50,
    ) -> str:
        """Traverse bounded graph relationships from one resolved symbol."""

        return invoke(
            service.traverse_graph_tool,
            target=target,
            direction=direction,
            max_depth=max_depth,
            max_results=max_results,
        )

    @tool
    async def get_impact_radius_tool(
        changed_files: list[str],
        max_depth: int = 2,
        max_results: int = 50,
    ) -> str:
        """Estimate dependent symbols and files for repository-relative paths."""

        return invoke(
            service.get_impact_radius_tool,
            changed_files=changed_files,
            max_depth=max_depth,
            max_results=max_results,
        )

    @tool
    async def list_flows_tool(limit: int = 20) -> str:
        """List bounded deterministic execution-flow summaries."""

        return invoke(service.list_flows_tool, limit=limit)

    @tool
    async def get_flow_tool(flow_id: int, max_steps: int = 100) -> str:
        """Inspect one stored execution flow and its member symbols."""

        return invoke(service.get_flow_tool, flow_id=flow_id, max_steps=max_steps)

    @tool
    async def get_affected_flows_tool(
        changed_files: list[str],
        max_flows: int = 20,
    ) -> str:
        """Find flows containing symbols from repository-relative paths."""

        return invoke(
            service.get_affected_flows_tool,
            changed_files=changed_files,
            max_flows=max_flows,
        )

    @tool
    async def list_communities_tool(
        limit: int = 20,
        max_members: int = 10,
    ) -> str:
        """List architectural communities with bounded symbol previews."""

        return invoke(
            service.list_communities_tool,
            limit=limit,
            max_members=max_members,
        )

    @tool
    async def get_community_tool(
        community_id: int,
        max_members: int = 50,
    ) -> str:
        """Inspect one stored architectural community."""

        return invoke(
            service.get_community_tool,
            community_id=community_id,
            max_members=max_members,
        )

    @tool
    async def get_architecture_overview_tool(max_communities: int = 10) -> str:
        """Summarize major communities and cross-community relationships."""

        return invoke(
            service.get_architecture_overview_tool,
            max_communities=max_communities,
        )

    @tool
    async def get_hub_nodes_tool(top_n: int = 10) -> str:
        """Rank highly connected graph symbols as potential hotspots."""

        return invoke(service.get_hub_nodes_tool, top_n=top_n)

    @tool
    async def get_bridge_nodes_tool(top_n: int = 10) -> str:
        """Rank symbols that bridge otherwise separated graph regions."""

        return invoke(service.get_bridge_nodes_tool, top_n=top_n)

    @tool
    async def get_knowledge_gaps_tool(max_results: int = 25) -> str:
        """Report bounded, structurally untested function hotspots."""

        return invoke(service.get_knowledge_gaps_tool, max_results=max_results)

    return [
        list_graph_stats_tool,
        get_minimal_context_tool,
        semantic_search_nodes_tool,
        query_graph_tool,
        traverse_graph_tool,
        get_impact_radius_tool,
        list_flows_tool,
        get_flow_tool,
        get_affected_flows_tool,
        list_communities_tool,
        get_community_tool,
        get_architecture_overview_tool,
        get_hub_nodes_tool,
        get_bridge_nodes_tool,
        get_knowledge_gaps_tool,
    ]


def _bounded_json(result: dict[str, object], *, max_chars: int) -> str:
    rendered = json.dumps(result, sort_keys=True, separators=(",", ":"))
    if len(rendered) <= max_chars:
        return rendered
    bounded = {
        key: value
        for key, value in result.items()
        if key not in {"data", "summary"}
    }
    bounded.update(
        {
            "summary": str(result.get("summary", ""))[:500],
            "returned": 0,
            "omitted": int(result.get("total", 0) or 0),
            "truncated": True,
            "data": {
                "notice": "Result exceeded the native tool character budget; narrow the query."
            },
        }
    )
    rendered = json.dumps(bounded, sort_keys=True, separators=(",", ":"))
    return rendered[:max_chars]
