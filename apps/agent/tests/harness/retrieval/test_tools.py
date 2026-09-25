from __future__ import annotations

import asyncio
import json
from pathlib import Path

from sage.harness.retrieval.tools import _bounded_json, build_retrieval_tools
from sage.harness.retrieval.service import RepositoryRetrievalService


EXPECTED_TOOLS = {
    "list_graph_stats_tool",
    "get_minimal_context_tool",
    "search_nodes_tool",
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
    "find_large_functions_tool", "get_surprising_connections_tool",
    "get_suggested_questions_tool", "refactor_tool",
    "detect_changes_tool", "get_review_context_tool",
}


def test_native_manifest_has_typed_bound_read_only_schemas(
    fixture_repo: Path,
    built_index: tuple[RepositoryRetrievalService, Path],
) -> None:
    service, index_file = built_index
    tools = build_retrieval_tools(
        service,
        repo_root=fixture_repo,
        index_file=index_file,
    )

    assert {item.name for item in tools} == EXPECTED_TOOLS
    for item in tools:
        schema = item.args_schema.model_json_schema()
        assert "repo_root" not in schema.get("properties", {})
        assert "index_file" not in schema.get("properties", {})
        assert "description" in schema


def test_every_native_adapter_invokes_and_returns_json(
    fixture_repo: Path,
    built_index: tuple[RepositoryRetrievalService, Path],
) -> None:
    service, index_file = built_index
    usage: list[tuple[str, dict[str, object], float]] = []
    tools = {
        item.name: item
        for item in build_retrieval_tools(
            service,
            repo_root=fixture_repo,
            index_file=index_file,
            usage_recorder=lambda name, result, duration: usage.append(
                (name, result, duration)
            ),
        )
    }
    calls = {
        "list_graph_stats_tool": {},
        "get_minimal_context_tool": {"task": "fix helper"},
        "search_nodes_tool": {"query": "helper"},
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
        "find_large_functions_tool": {"min_lines": 1},
        "get_surprising_connections_tool": {},
        "get_suggested_questions_tool": {},
        "refactor_tool": {"mode": "dead_code"},
        "detect_changes_tool": {"changed_files": ["service.py"]},
        "get_review_context_tool": {"changed_files": ["service.py"]},
    }

    for name, arguments in calls.items():
        payload = json.loads(asyncio.run(tools[name].ainvoke(arguments)))
        assert payload["status"] in {"ok", "ready", "not_found"}
        assert payload["indexed_sha"]
        assert payload["indexed_sha"]
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
        for item in build_retrieval_tools(
            RepositoryRetrievalService(),
            repo_root=fixture_repo,
            index_file=missing,
            output_chars=1_000,
        )
        if item.name == "search_nodes_tool"
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
    assert payload["returned"] == len(payload["data"]["nodes"]) > 0
    assert payload["omitted"] + payload["returned"] == 500


def test_review_context_uses_bound_source_reader_and_refactor_is_read_only(fixture_repo, built_index):
    from sage.harness.retrieval.tools import build_retrieval_tools

    service, database = built_index
    reads = []
    def reader(**kwargs):
        reads.append(kwargs)
        return "current bounded source"
    tools = {t.name: t for t in build_retrieval_tools(service, repo_root=fixture_repo, index_file=database, source_reader=reader)}
    response = json.loads(asyncio.run(tools["get_review_context_tool"].ainvoke({"changed_files": ["service.py"], "include_source": True})))
    assert response["data"]["changed_functions_total"] > 0
    assert response["data"]["source_snippets"][0]["source"] == "current bounded source"
    assert all(r["end_line"] - r["start_line"] < 50 for r in reads)
    original = (fixture_repo / "service.py").read_bytes()
    preview = json.loads(asyncio.run(tools["refactor_tool"].ainvoke({"mode": "rename", "old_name": "helper", "new_name": "helper_new"})))
    assert preview["returned"] > 0
    assert (fixture_repo / "service.py").read_bytes() == original
