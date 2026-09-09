from __future__ import annotations

from pathlib import Path

import pytest

from sage.errors import LegionMemoryQueryError
from sage.legion_memory.service import LegionMemoryService


def test_search_query_traversal_and_impact_return_provenance(
    fixture_repo: Path,
    built_memory: tuple[LegionMemoryService, Path],
) -> None:
    service, memory_file = built_memory
    search = service.semantic_search_nodes_tool(
        query="helper",
        repo_root=fixture_repo,
        memory_file=memory_file,
    )
    helper = search["data"]["nodes"][0]["qualified_name"]
    callers = service.query_graph_tool(
        pattern="callers_of",
        target=helper,
        repo_root=fixture_repo,
        memory_file=memory_file,
    )
    traversal = service.traverse_graph_tool(
        target=helper,
        direction="incoming",
        repo_root=fixture_repo,
        memory_file=memory_file,
    )
    impact = service.get_impact_radius_tool(
        changed_files=["service.py"],
        repo_root=fixture_repo,
        memory_file=memory_file,
    )

    assert search["search_mode"] == "fts"
    assert callers["returned"] >= 2
    assert traversal["returned"] >= 2
    assert impact["returned"] >= 1
    for result in (search, callers, traversal, impact):
        assert result["repository_id"]
        assert result["indexed_sha"]
        assert result["returned"] <= result["total"]


def test_flow_community_architecture_and_ranking_tools_work(
    fixture_repo: Path,
    built_memory: tuple[LegionMemoryService, Path],
) -> None:
    service, memory_file = built_memory
    arguments = {"repo_root": fixture_repo, "memory_file": memory_file}
    flows = service.list_flows_tool(**arguments)
    flow = service.get_flow_tool(
        flow_id=flows["data"]["flows"][0]["id"],
        **arguments,
    )
    affected = service.get_affected_flows_tool(
        changed_files=["service.py"],
        **arguments,
    )
    communities = service.list_communities_tool(**arguments)
    community = service.get_community_tool(
        community_id=communities["data"]["communities"][0]["id"],
        **arguments,
    )
    results = [
        flows,
        flow,
        affected,
        communities,
        community,
        service.get_architecture_overview_tool(**arguments),
        service.get_hub_nodes_tool(**arguments),
        service.get_bridge_nodes_tool(**arguments),
        service.get_knowledge_gaps_tool(**arguments),
        service.get_minimal_context_tool(task="fix helper", **arguments),
        service.list_graph_stats_tool(**arguments),
    ]

    assert all(result["status"] in {"ok", "ready"} for result in results)
    assert all(result["indexed_sha"] for result in results)


def test_empty_and_ambiguous_results_do_not_overclaim_certainty(
    fixture_repo: Path,
    built_memory: tuple[LegionMemoryService, Path],
) -> None:
    service, memory_file = built_memory
    arguments = {"repo_root": fixture_repo, "memory_file": memory_file}
    missing = service.query_graph_tool(
        pattern="references_to",
        target="not-present",
        **arguments,
    )
    unknown = service.traverse_graph_tool(target="not-present", **arguments)

    assert missing["returned"] == 0
    assert "verify in source" in missing["data"]["confidence"]
    assert unknown["status"] == "not_found"
    with pytest.raises(LegionMemoryQueryError, match="Unknown graph query"):
        service.query_graph_tool(pattern="arbitrary_sql", target="x", **arguments)
    with pytest.raises(LegionMemoryQueryError, match="repository-relative"):
        service.query_graph_tool(
            pattern="file_summary",
            target="../../etc/passwd",
            **arguments,
        )
