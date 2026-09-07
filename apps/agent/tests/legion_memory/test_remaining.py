"""Native reference-behavior regression tests; no model/network calls."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from sage.agents.repository_tools import build_repository_read_tools
from sage.legion_memory.parsing import CodeParser, PARSER_VERSION
from sage.legion_memory.queries import EDGE_PATTERNS, query_graph
from sage.legion_memory.session import MemorySession
from sage.legion_memory.store import GraphStore
from .conftest import commit_all


def apply_files(store, files, *, full=True, removed=()):
    parser = CodeParser()
    store.apply_update(parsed_files=[parser.parse_bytes(text.encode(), relative_path=path) for path, text in files.items()],
        removed_files=removed, repository_id="fixture", indexed_sha="sha",
        parser_version=PARSER_VERSION, build_type="full" if full else "incremental", full_rebuild=full)


def test_typescript_alias_reexport_callbacks_and_implements(tmp_path):
    files = {
        "tsconfig.json": '{"compilerOptions":{"baseUrl":".","paths":{"@/*":["src/*"]}}}',
        "src/repository.ts": "export interface Store { release(): void; }\nexport class Repo implements Store { release() { return 1; } }",
        "src/barrel.ts": "export { Repo as ProductRepo } from './repository';",
        "service.ts": "import {ProductRepo as Products} from '@/barrel';\nfunction cancel(repo: Products) { return repo.release(); }\nfunction route() { register(cancel); }",
        "other.ts": "class Other { release() { return 2; } }",
    }
    with GraphStore(tmp_path / "graph.sqlite3") as store:
        apply_files(store, files)
        calls = store.rows("SELECT target_qualified FROM edges WHERE kind='CALLS' AND source_qualified='service.ts::cancel'")
        assert calls == [{"target_qualified": "src/repository.ts::Repo.release"}]
        references = query_graph(store, "references_to", "service.ts::cancel", 5)
        assert any(r["qualified_name"] == "service.ts::route" for r in references["data"]["results"])
        inherited = query_graph(store, "inheritors_of", "src/repository.ts::Store", 5)
        assert inherited["data"]["results"][0]["edge_kind"] == "IMPLEMENTS"


def test_python_reexports_local_aliases_inheritance_and_shadowing(tmp_path):
    files = {
        "pkg/impl.py": "class Repo:\n    def release(self):\n        return 1\n",
        "pkg/__init__.py": "from .impl import Repo\n",
        "service.py": "from pkg import Repo as Products\nclass Child(Products):\n    def cancel(self):\n        return self.release()\ndef work():\n    from pkg import Repo as Local\n    return Local().release()\ndef register(release):\n    return release()\n",
        "other.py": "def release():\n    return 2\n",
    }
    with GraphStore(tmp_path / "graph.sqlite3") as store:
        apply_files(store, files)
        calls = store.rows("SELECT source_qualified,target_qualified FROM edges WHERE kind='CALLS' AND target_qualified LIKE '%release%' ORDER BY source_qualified")
        assert {r["target_qualified"] for r in calls if r["source_qualified"] != "service.py::register"} == {"pkg/impl.py::Repo.release"}
        assert next(r for r in calls if r["source_qualified"] == "service.py::register")["target_qualified"] == "release"


def test_java_receivers_and_package_isolated_spring_events(tmp_path):
    files = {
        "a/Repo.java": "package a; public class Repo { public void release() {} }",
        "b/Repo.java": "package b; public class Repo { public void release() {} }",
        "a/Created.java": "package a; public class Created {}",
        "b/Created.java": "package b; public class Created {}",
        "app/Service.java": '''package app;
import a.Repo;
import a.Created;
class Service {
    Repo repo;
    @PostMapping("/orders")
    void checkout() { this.repo.release(); events.publishEvent(new Created()); }
    @EventListener
    void receive(Created event) { repo.release(); }
}''',
        "other/Listener.java": "package other; import b.Created; class Listener { @EventListener void receive(Created event) {} }",
    }
    with GraphStore(tmp_path / "graph.sqlite3") as store:
        apply_files(store, files)
        assert "a/Repo.java::Repo.release" in {r["qualified_name"] for r in query_graph(store, "callees_of", "app/Service.java::Service.checkout", 20)["data"]["results"]}
        handlers = query_graph(store, "triggers_of", "app/Service.java::Service.checkout", 20)
        assert [r["qualified_name"] for r in handlers["data"]["results"]] == ["app/Service.java::Service.receive"]
        assert query_graph(store, "endpoints_for", "app/Service.java::Service.checkout", 5)["returned"] == 1


def test_go_and_rust_typed_static_receivers(tmp_path):
    files = {
        "repo.go": "package main\ntype Repo struct {}\nfunc (r *Repo) Release() {}\nfunc Run(repo *Repo) { repo.Release() }\n",
        "other.go": "package main\ntype Other struct {}\nfunc (r *Other) Release() {}\n",
        "a.rs": "pub struct Repo; impl Repo { pub fn new() -> Self { Repo } pub fn release(&self) {} }",
        "b.rs": "pub struct Other; impl Other { pub fn release(&self) {} }",
        "service.rs": "use crate::a::Repo as Store; fn run() { let repo = Store::new(); repo.release(); }",
    }
    with GraphStore(tmp_path / "graph.sqlite3") as store:
        apply_files(store, files)
        assert query_graph(store, "callees_of", "repo.go::Run", 5)["data"]["results"][0]["qualified_name"] == "repo.go::Repo.Release"
        calls = query_graph(store, "callees_of", "service.rs::run", 10)["data"]["results"]
        assert {r["qualified_name"] for r in calls} == {"a.rs::Repo.new", "a.rs::Repo.release"}


def test_routes_events_config_queries_and_incremental_removal(tmp_path):
    files = {"app.js": '''function checkout() { return save(); }
function save() { return 1; }
function notify() { bus.emit("ordered"); }
router.post("/checkout", checkout);
bus.on("ordered", checkout);
''', "config.py": 'import os\ndef connect():\n    return os.getenv("DB_HOST")\n'}
    with GraphStore(tmp_path / "graph.sqlite3") as store:
        apply_files(store, files)
        assert query_graph(store, "handlers_of", "POST /checkout", 5)["returned"] == 1
        assert query_graph(store, "endpoints_for", "checkout", 5)["returned"] == 1
        assert query_graph(store, "publishers_of", "event::ordered", 5)["returned"] == 1
        assert query_graph(store, "listeners_of", "event::ordered", 5)["returned"] == 1
        assert query_graph(store, "triggers_of", "notify", 5)["data"]["results"][0]["name"] == "checkout"
        assert query_graph(store, "triggered_by", "checkout", 5)["returned"] == 1
        assert query_graph(store, "consumers_of", "DB_HOST", 5)["returned"] == 1
        assert store.node("event::ordered")["kind"] == "Event"
        assert store.node("config:DB_HOST")["kind"] == "ConfigKey"
        apply_files(store, {"app.js": 'function notify() { bus.emit("ordered"); }'}, full=False)
        assert not store.rows("SELECT * FROM edges WHERE kind='TRIGGERS'")
        apply_files(store, {}, full=False, removed=("app.js", "config.py"))
        assert not store.rows("SELECT * FROM nodes WHERE kind IN ('Endpoint','Event','ConfigKey')")


def test_virtual_identity_moves_to_surviving_owner(tmp_path):
    with GraphStore(tmp_path / "graph.sqlite3") as store:
        apply_files(store, {"a.js": 'function send() { bus.emit("ordered"); }',
                            "b.js": 'function receive() {}\nbus.on("ordered", receive);'})
        assert store.node("event::ordered")["file_path"] == "a.js"
        apply_files(store, {}, full=False, removed=("a.js",))
        assert store.node("event::ordered")["file_path"] == "b.js"
        assert query_graph(store, "listeners_of", "ordered", 5)["returned"] == 1
        assert not store.rows("SELECT * FROM edges WHERE kind='TRIGGERS'")


def test_import_shadowing_super_and_flask_method_metadata(tmp_path):
    with GraphStore(tmp_path / "graph.sqlite3") as store:
        apply_files(store, {
            "base.py": "def callback():\n    pass\nclass Base:\n    def cancel(self):\n        pass\n",
            "app.py": "from base import Base, callback\nclass Child(Base):\n    def cancel(self):\n        return super().cancel()\ndef call(callback):\n    return callback()\n@app.route('/orders', methods=['POST', 'DELETE'])\ndef checkout():\n    return 1\n",
        })
        assert "base.py::Base.cancel" in {r["qualified_name"] for r in query_graph(store, "callees_of", "app.py::Child.cancel", 5)["data"]["results"]}
        callback = query_graph(store, "callees_of", "app.py::call", 5)["data"]["results"][0]
        assert callback["resolved"] is False
        assert query_graph(store, "handlers_of", "POST /orders", 5)["returned"] == 1
        assert query_graph(store, "handlers_of", "DELETE /orders", 5)["returned"] == 1
        assert query_graph(store, "handlers_of", "GET /orders", 5)["returned"] == 0


def test_transitive_tests_cycles_and_exact_totals(tmp_path):
    with GraphStore(tmp_path / "graph.sqlite3") as store:
        apply_files(store, {"service.py": "def inner():\n    return outer()\ndef outer():\n    return inner()\n",
                            "test_service.py": "from service import outer\ndef test_first():\n    outer()\ndef test_second():\n    outer()\n"})
        result = query_graph(store, "tests_for", "inner", 1)
        assert result["total"] == 2 and result["returned"] == 1


def test_converged_alias_edges_survive_incremental_retargeting(tmp_path):
    files = {"impl.py": "def run():\n    return 1\n",
             "barrel.py": "from impl import run\n",
             "app.py": "from impl import run as first\nfrom barrel import run as second\ndef work():\n    first(); second()\n"}
    with GraphStore(tmp_path / "graph.sqlite3") as store:
        apply_files(store, files)
        assert len(store.rows("SELECT * FROM edges WHERE kind='CALLS' AND source_qualified='app.py::work' AND target_qualified='impl.py::run'")) == 2
        apply_files(store, {"barrel.py": "def run():\n    return 2\n"}, full=False)
        assert {r["qualified_name"] for r in query_graph(store, "callees_of", "app.py::work", 5)["data"]["results"]} == {"impl.py::run", "barrel.py::run"}


def test_all_query_patterns_bound_results_and_report_ambiguity(tmp_path):
    with GraphStore(tmp_path / "graph.sqlite3") as store:
        apply_files(store, {"a.py": "def same():\n    pass\n", "b.py": "def same():\n    pass\n"})
        for pattern in EDGE_PATTERNS:
            result = query_graph(store, pattern, "same", 1)
            assert result["status"] == "ambiguous"
            assert result["total"] == 2 and result["returned"] == 1
        result = query_graph(store, "file_summary", "a.py", 1)
        assert result["total"] == 2 and result["returned"] == 1


def test_enrichment_preserves_source_deduplicates_and_records_usage(fixture_repo, built_memory):
    service, database = built_memory
    build = service.build_or_update_graph_tool(repo_root=fixture_repo, memory_file=database)
    retrieval = service.retrieve_issue_context(issue_text="helper", repo_root=fixture_repo, memory_file=database)
    session = MemorySession(service, fixture_repo, database, database, build, retrieval)
    repository = SimpleNamespace(read_file=lambda **_: "8: def helper():\n9:     return 42", search_text=lambda **_: "service.py:8:def helper():")
    tools = {t.name: t for t in build_repository_read_tools(SimpleNamespace(repository=repository), enrich=session.enrich)}
    first = asyncio.run(tools["read_file"].ainvoke({"path": "service.py", "start_line": 8, "end_line": 9}))
    assert first.startswith("8: def helper():")
    assert "Called by:" in first and "Tests:" in first and "Community:" in first
    assert "Accepted-base" in first
    second = asyncio.run(tools["read_file"].ainvoke({"path": "service.py", "start_line": 8, "end_line": 9}))
    assert "legion-read-context" not in second
    assert session.artifact().enrichments[0].hit_count == 1
    assert session.artifact().enrichments[1].status == "skipped"
    assert not session.artifact().tool_calls
    assert session.enrich(tool_name="read_file", path="service.py", available_chars=100) == ""
    session.close()
    assert session.enrich(tool_name="read_file", path="service.py", available_chars=3000) == ""


def test_enrichment_failure_leaves_source_unchanged(fixture_repo, built_memory):
    service, database = built_memory
    build = service.build_or_update_graph_tool(repo_root=fixture_repo, memory_file=database)
    retrieval = service.retrieve_issue_context(issue_text="helper", repo_root=fixture_repo, memory_file=database)
    session = MemorySession(service, fixture_repo, database, database, build, retrieval)
    (fixture_repo / "new.py").write_text("def new():\n    pass\n")
    commit_all(fixture_repo, "make graph stale")
    assert session.enrich(tool_name="read_file", path="service.py", available_chars=3000) == ""
    assert session.artifact().enrichments[0].status == "unavailable"


def test_search_enrichment_and_default_read_range(fixture_repo, built_memory):
    service, database = built_memory
    build = service.build_or_update_graph_tool(repo_root=fixture_repo, memory_file=database)
    retrieval = service.retrieve_issue_context(issue_text="helper", repo_root=fixture_repo, memory_file=database)
    session = MemorySession(service, fixture_repo, database, database, build, retrieval)
    repository = SimpleNamespace(search_text=lambda **_: "service.py:8:def helper():", read_file=lambda **_: "source")
    tools = {t.name: t for t in build_repository_read_tools(SimpleNamespace(repository=repository), enrich=session.enrich)}
    result = asyncio.run(tools["search_text"].ainvoke({"query": "helper"}))
    assert result.startswith("service.py:8:def helper():") and "Called by:" in result
    assert session.artifact().enrichments[0].status == "used"
    captures = []
    def capture(**kwargs):
        captures.append(kwargs)
        return ""
    tools = {t.name: t for t in build_repository_read_tools(SimpleNamespace(repository=repository), enrich=capture)}
    asyncio.run(tools["read_file"].ainvoke({"path": "service.py", "start_line": 12}))
    assert captures[0]["end_line"] == 311


def test_review_context_uses_bound_source_reader_and_refactor_is_read_only(fixture_repo, built_memory):
    from sage.agents.memory_tools import build_legion_memory_tools

    service, database = built_memory
    reads = []
    def reader(**kwargs):
        reads.append(kwargs)
        return "current bounded source"
    tools = {t.name: t for t in build_legion_memory_tools(service, repo_root=fixture_repo, memory_file=database, source_reader=reader)}
    response = json.loads(asyncio.run(tools["get_review_context_tool"].ainvoke({"changed_files": ["service.py"], "include_source": True})))
    assert response["data"]["changed_functions_total"] > 0
    assert response["data"]["source_snippets"][0]["source"] == "current bounded source"
    assert all(r["end_line"] - r["start_line"] < 50 for r in reads)
    original = (fixture_repo / "service.py").read_bytes()
    preview = json.loads(asyncio.run(tools["refactor_tool"].ainvoke({"mode": "rename", "old_name": "helper", "new_name": "helper_new"})))
    assert preview["returned"] > 0
    assert (fixture_repo / "service.py").read_bytes() == original
