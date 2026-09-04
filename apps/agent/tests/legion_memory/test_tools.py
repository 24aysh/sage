from __future__ import annotations

import asyncio
import json
from pathlib import Path

from sage.agents.memory_tools import _bounded_json, build_legion_memory_tools
from sage.legion_memory.service import LegionMemoryService


EXPECTED_TOOLS = {
    "list_graph_stats_tool",
    "get_minimal_context_tool",
    "semantic_search_nodes_tool",
    "query_graph_tool",
    "traverse_graph_tool",
    "get_impact_radius_tool",
    "list_flows_tool",
    "get_flow_tool",
    "get_affected_flows_tool",
    "list_communities_tool",
    "get_community_tool",
    "get_architecture_overview_tool",
    "get_hub_nodes_tool",
    "get_bridge_nodes_tool",
    "get_knowledge_gaps_tool",
}


def test_native_manifest_has_typed_bound_read_only_schemas(
    fixture_repo: Path,
    built_memory: tuple[LegionMemoryService, Path],
) -> None:
    service, memory_file = built_memory
    tools = build_legion_memory_tools(
        service,
        repo_root=fixture_repo,
        memory_file=memory_file,
    )

    assert {item.name for item in tools} == EXPECTED_TOOLS
    for item in tools:
        schema = item.args_schema.model_json_schema()
        assert "repo_root" not in schema.get("properties", {})
        assert "memory_file" not in schema.get("properties", {})
        assert "description" in schema


def test_every_native_adapter_invokes_and_returns_json(
    fixture_repo: Path,
    built_memory: tuple[LegionMemoryService, Path],
) -> None:
    service, memory_file = built_memory
    usage: list[tuple[str, dict[str, object], float]] = []
    tools = {
        item.name: item
        for item in build_legion_memory_tools(
            service,
            repo_root=fixture_repo,
            memory_file=memory_file,
            usage_recorder=lambda name, result, duration: usage.append(
                (name, result, duration)
            ),
        )
    }
    calls = {
        "list_graph_stats_tool": {},
        "get_minimal_context_tool": {"task": "fix helper"},
        "semantic_search_nodes_tool": {"query": "helper"},
        "query_graph_tool": {"pattern": "callers_of", "target": "helper"},
        "traverse_graph_tool": {"target": "helper"},
        "get_impact_radius_tool": {"changed_files": ["service.py"]},
        "list_flows_tool": {},
        "get_flow_tool": {"flow_id": 1},
        "get_affected_flows_tool": {"changed_files": ["service.py"]},
        "list_communities_tool": {},
        "get_community_tool": {"community_id": 1},
        "get_architecture_overview_tool": {},
        "get_hub_nodes_tool": {},
        "get_bridge_nodes_tool": {},
        "get_knowledge_gaps_tool": {},
    }

    for name, arguments in calls.items():
        payload = json.loads(asyncio.run(tools[name].ainvoke(arguments)))
        assert payload["status"] in {"ok", "ready", "not_found"}
        assert payload["indexed_sha"]
        assert payload["repository_id"]
        assert payload["returned"] <= payload["total"]

    assert len(usage) == len(EXPECTED_TOOLS)
    assert {name for name, _, _ in usage} == EXPECTED_TOOLS
    assert all(duration >= 0 for _, _, duration in usage)


def test_adapters_report_unavailable_graph_and_bound_output(
    fixture_repo: Path,
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing.sqlite3"
    tool = next(
        item
        for item in build_legion_memory_tools(
            LegionMemoryService(),
            repo_root=fixture_repo,
            memory_file=missing,
            output_chars=1_000,
        )
        if item.name == "semantic_search_nodes_tool"
    )

    rendered = asyncio.run(tool.ainvoke({"query": "x"}))
    payload = json.loads(rendered)

    assert len(rendered) <= 1_000
    assert payload["status"] == "unavailable"
    assert payload["returned"] == 0


def test_large_tool_payload_remains_valid_json_within_budget() -> None:
    rendered = _bounded_json(
        {
            "status": "ok",
            "summary": "many results",
            "repository_id": "r" * 64,
            "indexed_sha": "a" * 40,
            "last_updated": "2026-09-04T00:00:00+00:00",
            "total": 500,
            "returned": 500,
            "omitted": 0,
            "truncated": False,
            "data": {"nodes": ["x" * 200 for _ in range(500)]},
        },
        max_chars=1_000,
    )

    payload = json.loads(rendered)
    assert len(rendered) <= 1_000
    assert payload["truncated"] is True
    assert payload["returned"] == 0
    assert payload["omitted"] == 500
