from pathlib import Path

from sage.domain.memory import (
    MemoryBuildResult,
    MemoryBuildType,
    MemoryRetrievalOutcome,
    MemoryRetrievalResult,
    MemoryRetrievalStatus,
)
from sage.legion_memory.service import LegionMemoryService
from sage.legion_memory.session import MemorySession


def test_session_records_bounded_tool_evidence_without_payloads(tmp_path: Path) -> None:
    memory_file = tmp_path / "graph.sqlite3"
    session = MemorySession(
        service=LegionMemoryService(),
        repo_root=tmp_path,
        requested_memory_file=memory_file,
        memory_file=memory_file,
        build=_build(memory_file),
        retrieval=_retrieval(memory_file),
    )

    session.record_tool_call(
        "semantic_search_nodes_tool",
        {
            "status": "ok",
            "returned": 3,
            "truncated": True,
            "data": {
                "nodes": [
                    {"file_path": "src/app.py", "body": "private source"},
                    {"file_path": "../../outside", "body": "more private source"},
                ],
                "impacted_files": ["tests/test_app.py"],
            },
        },
        2.5,
    )

    artifact = session.artifact()
    record = artifact.tool_calls[0]
    assert record.tool_name == "semantic_search_nodes_tool"
    assert record.hit_count == 3
    assert record.returned_paths == ("src/app.py", "tests/test_app.py")
    assert record.truncated is True
    assert "private source" not in artifact.model_dump_json()

    session.close()
    session.record_tool_call("late_tool", {"status": "ok"}, 1)
    assert len(session.tool_calls) == 1


def _build(memory_file: Path) -> MemoryBuildResult:
    return MemoryBuildResult(
        build_type=MemoryBuildType.FULL,
        memory_file=memory_file,
        repository_id="repository-id",
        indexed_sha="a" * 40,
        schema_version=1,
        files_indexed=1,
        files_parsed=1,
        files_removed=0,
        total_nodes=2,
        total_edges=1,
        total_flows=0,
        total_communities=0,
        duration_ms=1,
    )


def _retrieval(memory_file: Path) -> MemoryRetrievalResult:
    return MemoryRetrievalResult(
        status=MemoryRetrievalStatus.NO_MATCH,
        outcome=MemoryRetrievalOutcome.NO_LEXICAL_CANDIDATES,
        summary="No match.",
        memory_file=memory_file,
        repository_id="repository-id",
        indexed_sha="a" * 40,
        duration_ms=1,
    )
