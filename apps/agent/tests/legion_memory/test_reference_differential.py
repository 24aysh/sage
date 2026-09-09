"""Small normalized structural certification, not an agent benchmark."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from sage.legion_memory.store import GraphStore
from .conftest import apply_files

HERE = Path(__file__).parent


def fixture():
    return json.loads((HERE / "fixtures/navigation_golden.json").read_text())


def native_snapshot(store):
    return {
        "nodes": [list(row.values()) for row in store.rows(
            "SELECT qualified_name,kind,file_path,line_start,line_end FROM nodes WHERE kind!='File' ORDER BY qualified_name")],
        "calls": [list(row.values()) for row in store.rows(
            "SELECT DISTINCT e.source_qualified,e.target_qualified FROM edges e JOIN nodes n ON n.qualified_name=e.target_qualified "
            "WHERE e.kind='CALLS' ORDER BY e.source_qualified,e.target_qualified")],
        "flows": sorted(sorted(json.loads(r["path_json"])) for r in store.rows("SELECT path_json FROM flows")),
        "communities": sorted(sorted(json.loads(r["members_json"])) for r in store.rows("SELECT members_json FROM communities")),
        "query": sorted(n["qualified_name"] for n in store.search("helper", kind=None, limit=10)[0]),
    }


def test_offline_navigation_golden(tmp_path):
    data = fixture()
    with GraphStore(tmp_path / "graph.sqlite3") as store:
        apply_files(store, data["files"])
        assert native_snapshot(store) == data["expected"]


def test_optional_pinned_reference(request, tmp_path, monkeypatch):
    reference = request.config.getoption("--legion-reference")
    if reference is None:
        pytest.skip("Optional pinned-reference checkout not requested")
    root = Path(reference).resolve()
    manifest = json.loads((HERE / "reference_manifest.json").read_text())
    for name, digest in manifest["sha256"].items():
        assert hashlib.sha256((root / "code_review_graph" / name).read_bytes()).hexdigest() == digest, name
    monkeypatch.syspath_prepend(str(root))
    from code_review_graph.parser import CodeParser
    from code_review_graph.graph import GraphStore as ReferenceStore
    from code_review_graph.flows import trace_flows
    from code_review_graph.communities import detect_communities

    data = fixture()
    source = tmp_path / "source"
    source.mkdir()
    parser = CodeParser(repo_root=source)
    with ReferenceStore(tmp_path / "reference.sqlite3") as store:
        for path, text in data["files"].items():
            location = source / path
            location.parent.mkdir(parents=True, exist_ok=True)
            location.write_text(text)
            nodes, edges = parser.parse_file(location)
            for node in nodes:
                store.upsert_node(node)
            for edge in edges:
                store.upsert_edge(edge)
        store.commit()
        store.resolve_bare_call_targets()
        nodes = store.get_all_nodes()
        names = {n.id: n.qualified_name for n in nodes}
        def normalize(value):
            return value.removeprefix(source.as_posix() + "/")
        snapshot = {
            "nodes": sorted([normalize(n.qualified_name), n.kind, normalize(n.file_path), n.line_start, n.line_end] for n in nodes),
            "calls": sorted({(normalize(e.source_qualified), normalize(e.target_qualified)) for e in store.get_all_edges()
                             if e.kind == "CALLS" and e.target_qualified in names.values()}),
            "flows": sorted(sorted(normalize(names[n]) for n in flow["path"]) for flow in trace_flows(store)),
            "communities": sorted(sorted(normalize(name) for name in group["members"]) for group in detect_communities(store)),
            "query": sorted(normalize(n.qualified_name) for n in store.search_nodes("helper")),
        }
        assert json.loads(json.dumps(snapshot)) == data["expected"]
