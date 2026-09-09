"""Static symbol binding, language dispatch, and incremental resolution."""

import json

from sage.legion_memory.queries import query_graph
from sage.legion_memory.store import GraphStore
from sage.legion_memory.vectors import node_text
from .conftest import apply_files


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


def test_converged_alias_edges_survive_incremental_retargeting(tmp_path):
    files = {"impl.py": "def run():\n    return 1\n",
             "barrel.py": "from impl import run\n",
             "app.py": "from impl import run as first\nfrom barrel import run as second\ndef work():\n    first(); second()\n"}
    with GraphStore(tmp_path / "graph.sqlite3") as store:
        apply_files(store, files)
        assert len(store.rows("SELECT * FROM edges WHERE kind='CALLS' AND source_qualified='app.py::work' AND target_qualified='impl.py::run'")) == 2
        apply_files(store, {"barrel.py": "def run():\n    return 2\n"}, full=False)
        assert {r["qualified_name"] for r in query_graph(store, "callees_of", "app.py::work", 5)["data"]["results"]} == {"impl.py::run", "barrel.py::run"}


def test_python_metadata_and_injected_receiver_resolve_duplicate_methods(tmp_path):
    files = {
        "products.py": "class ProductRepository:\n    def release(self, product_id: str) -> None:\n        pass\n",
        "orders.py": "class OrderRepository:\n    def release(self):\n        pass\n",
        "service.py": '''from products import ProductRepository as Products

class Service:
    def __init__(self, products: Products):
        self.products = products

    def cancel(self, product_id: str) -> bool:
        """Restore stock after revoking an order."""
        self.products.release(product_id)
        return True
''',
    }
    with GraphStore(tmp_path / "graph.sqlite3") as store:
        apply_files(store, files)
        cancel = store.rows("SELECT * FROM nodes WHERE qualified_name='service.py::Service.cancel'")[0]
        extra = json.loads(cancel["extra_json"])
        assert "Restore stock" in extra["docstring"]
        assert "product_id: str" in extra["params"]
        assert extra["return_type"] == "bool"
        text = node_text(cancel)
        assert "Service.cancel" in text
        assert "Restore stock" in text
        edges = store.rows("SELECT target_qualified FROM edges WHERE source_qualified='service.py::Service.cancel' AND kind='CALLS'")
        assert edges == [{"target_qualified": "products.py::ProductRepository.release"}]


def test_incremental_resolution_matches_full_when_target_becomes_ambiguous(tmp_path):
    first = {"caller.py": "def run():\n    work()\n", "a.py": "def work():\n    pass\n"}
    added = {"b.py": "def work():\n    pass\n"}
    with GraphStore(tmp_path / "incremental.sqlite3") as incremental, GraphStore(tmp_path / "full.sqlite3") as full:
        apply_files(incremental, first)
        assert incremental.rows("SELECT target_qualified FROM edges WHERE kind='CALLS'")[0]["target_qualified"] == "a.py::work"
        apply_files(incremental, added, full=False)
        apply_files(full, first | added)
        query = "SELECT kind, source_qualified, target_qualified FROM edges ORDER BY kind, source_qualified, target_qualified"
        assert incremental.rows(query) == full.rows(query)
        assert incremental.rows("SELECT target_qualified FROM edges WHERE kind='CALLS'") == [{"target_qualified": "work"}]


def test_relative_import_does_not_bind_wrong_top_level_module(tmp_path):
    files = {
        "target.py": "def work():\n    pass\n",
        "pkg/target.py": "def work():\n    pass\n",
        "pkg/caller.py": "from .target import work as execute\ndef run():\n    execute()\n",
    }
    with GraphStore(tmp_path / "graph.sqlite3") as store:
        apply_files(store, files)
        assert store.rows("SELECT target_qualified FROM edges WHERE kind='CALLS'") == [{"target_qualified": "pkg/target.py::work"}]


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
        apply_files(store, files)
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
        apply_files(store, files)
        rows = store.rows("SELECT target_qualified FROM edges WHERE kind='CALLS' AND source_qualified='app.py::Service.process'")
        assert rows == [{"target_qualified": "save"}]


def test_jsonc_inherited_paths_resolve_relative_to_declaring_config(tmp_path):
    files = {
        "tsconfig.base.jsonc": '{/* base */ "compilerOptions": {"baseUrl": ".", "paths": {"@lib/*": ["lib/*"],},},}',
        "src/tsconfig.json": '{"extends": "../tsconfig.base.jsonc"}',
        "lib/save.ts": 'export function save() { return 1; }',
        "src/app.ts": 'import { save } from "@lib/save"; export function main() { return save(); }',
    }
    with GraphStore(tmp_path / "graph.sqlite3") as store:
        apply_files(store, files)
        assert store.rows("SELECT 1 FROM edges WHERE kind='CALLS' AND source_qualified='src/app.ts::main' "
                          "AND target_qualified='lib/save.ts::save'")


def test_jsonc_preserves_strings_and_rejects_external_extends():
    import pytest
    from sage.legion_memory.tsconfig import parse_tsconfig
    assert parse_tsconfig(b'{"compilerOptions":{"paths":{"url":["https://host/*"]}}}')['paths']['url'] == ['https://host/*']
    with pytest.raises(ValueError, match="relative"):
        parse_tsconfig(b'{"extends":"some-package/config"}')
