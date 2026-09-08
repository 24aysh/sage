from pathlib import Path

from sage.domain.embeddings import VectorStatus
from sage.domain.memory import MemoryRetrievalBudgets
from sage.legion_memory.parsing import CodeParser, PARSER_VERSION
from sage.legion_memory.retrieval import retrieve_issue_context
from sage.legion_memory.store import GraphStore


def build(store, files):
    store.apply_update(
        parsed_files=[CodeParser().parse_bytes(source.encode(), relative_path=path)
                      for path, source in files.items()],
        removed_files=[], repository_id="test", indexed_sha="sha",
        parser_version=PARSER_VERSION, build_type="full", full_rebuild=True,
    )


def test_semantic_distractor_does_not_evict_explicit_symbol(tmp_path: Path):
    class Vectors:
        def search(self, store, query, *, limit):
            assert len(query) <= 1200
            return [("config.py::settings", .8)], VectorStatus(status="ready")

    with GraphStore(tmp_path / "graph.sqlite3") as store:
        build(store, {"service.py": "class WebhookService:\n    def process(self):\n        pass\n",
                      "config.py": "def settings():\n    return 1\n"})
        result = retrieve_issue_context("Fix `WebhookService.process` retry handling.", store,
            memory_file=store.path, budgets=MemoryRetrievalBudgets(), vectors=Vectors())
        assert result.items[0].qualified_name == "service.py::WebhookService.process"
        assert "semantic" in result.search_modes
        assert any(d.channel_ranks.get("lexical") for d in result.diagnostics)
        assert result.duration_ms >= result.ranking_duration_ms


def test_unresolved_edge_is_not_rebound_to_a_test_double(tmp_path: Path):
    with GraphStore(tmp_path / "graph.sqlite3") as store:
        build(store, {"repo.py": "class Repo:\n    def claim(self):\n        self.collection.insert_one({})\n",
                      "tests/fakes.py": "class FakeCollection:\n    def insert_one(self, data):\n        pass\n"})
        assert store.node("insert_one") is not None
        assert store.exact_node("insert_one") is None
        result = retrieve_issue_context("Fix `Repo.claim`.", store,
            memory_file=store.path, budgets=MemoryRetrievalBudgets())
        assert not any(i.name == "insert_one" and any(r.relationship == "CALLS" for r in i.relationships)
                       for i in result.items)
        assert result.unresolved_edges > 0


def test_constructor_factory_registry_and_blueprint_path(tmp_path):
    files = {
        "repo.py": "class Events:\n    def claim(self):\n        pass\n",
        "audit.py": "class Audit:\n    def record(self):\n        pass\n",
        "outbox.py": "class Outbox:\n    def enqueue(self):\n        pass\n",
        "service.py": "class Service:\n    def __init__(self, events, audit, outbox):\n        self.events = events\n        self.audit = audit\n        self.outbox = outbox\n    def process(self):\n        self.events.claim()\n        self.audit.record()\n        self.outbox.enqueue()\n",
        "app.py": '''from flask import Flask
from repo import Events
from service import Service
from audit import Audit
from outbox import Outbox
def create_app():
    app = Flask(__name__)
    app.extensions["service"] = Service(Events(), Audit(), Outbox())
    return app
''',
        "routes.py": '''from flask import current_app, Blueprint
bp = Blueprint("hooks", __name__, url_prefix="/api/hooks")
def service():
    return current_app.extensions["service"]
@bp.post("/events")
def receive():
    service().process()
''',
        "tests/test_routes.py": 'def test_receive(client):\n    client.post("/api/hooks/events")\n',
    }
    with GraphStore(tmp_path / "graph.sqlite3") as store:
        build(store, files)
        calls = {(r["source_qualified"], r["target_qualified"]) for r in store.rows("SELECT * FROM edges WHERE kind='CALLS'")}
        assert ("routes.py::receive", "service.py::Service.process") in calls
        assert ("service.py::Service.process", "repo.py::Events.claim") in calls
        assert ("service.py::Service.process", "audit.py::Audit.record") in calls
        assert ("service.py::Service.process", "outbox.py::Outbox.enqueue") in calls
        endpoints = store.rows("SELECT * FROM nodes WHERE kind='Endpoint'")
        assert len(endpoints) == 1
        assert "/api/hooks/events" in endpoints[0]["name"]
        assert store.rows("SELECT 1 FROM flow_memberships m JOIN flows f ON f.id=m.flow_id "
                          "WHERE f.entry_qualified='routes.py::receive' AND m.qualified_name='repo.py::Events.claim'")


def test_conflicting_constructor_sites_remain_unresolved(tmp_path):
    files = {"app.py": '''class A:
    def save(self):
        pass
class B:
    def save(self):
        pass
class Service:
    def __init__(self, repo):
        self.repo = repo
    def process(self):
        self.repo.save()
def first():
    return Service(A())
def second():
    return Service(B())
'''}
    with GraphStore(tmp_path / "graph.sqlite3") as store:
        build(store, files)
        rows = store.rows("SELECT target_qualified FROM edges WHERE kind='CALLS' AND source_qualified='app.py::Service.process'")
        assert rows == [{"target_qualified": "save"}]


def test_nested_fixture_does_not_index_ancestor(fixture_repo):
    import pytest
    from sage.errors import LegionMemoryBuildError
    from sage.legion_memory.service import LegionMemoryService
    nested = fixture_repo / "standalone"
    nested.mkdir()
    with pytest.raises(LegionMemoryBuildError, match="ancestor Git root"):
        LegionMemoryService().build_or_update_graph_tool(repo_root=nested)
    assert not (nested / ".git").exists()


def test_jsonc_inherited_paths_resolve_relative_to_declaring_config(tmp_path):
    files = {
        "tsconfig.base.jsonc": '{/* base */ "compilerOptions": {"baseUrl": ".", "paths": {"@lib/*": ["lib/*"],},},}',
        "src/tsconfig.json": '{"extends": "../tsconfig.base.jsonc"}',
        "lib/save.ts": 'export function save() { return 1; }',
        "src/app.ts": 'import { save } from "@lib/save"; export function main() { return save(); }',
    }
    with GraphStore(tmp_path / "graph.sqlite3") as store:
        build(store, files)
        assert store.rows("SELECT 1 FROM edges WHERE kind='CALLS' AND source_qualified='src/app.ts::main' "
                          "AND target_qualified='lib/save.ts::save'")


def test_jsonc_preserves_strings_and_rejects_external_extends():
    import pytest
    from sage.legion_memory.tsconfig import parse_tsconfig
    assert parse_tsconfig(b'{"compilerOptions":{"paths":{"url":["https://host/*"]}}}')['paths']['url'] == ['https://host/*']
    with pytest.raises(ValueError, match="relative"):
        parse_tsconfig(b'{"extends":"some-package/config"}')
