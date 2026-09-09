"""Reference-derived behavior fixtures, independent of the reference checkout."""

from __future__ import annotations

import json

from sage.legion_memory.parsing import CodeParser, PARSER_VERSION
from sage.legion_memory.store import GraphStore
from sage.legion_memory.vectors import node_text


def build(store, files, *, full=True):
    parser = CodeParser()
    store.apply_update(parsed_files=[parser.parse_bytes(text.encode(), relative_path=path) for path, text in files.items()],
        removed_files=[], repository_id="fixture", indexed_sha="fixture-sha",
        parser_version=PARSER_VERSION, build_type="full" if full else "incremental", full_rebuild=full)


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
        build(store, files)
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
        build(incremental, first)
        assert incremental.rows("SELECT target_qualified FROM edges WHERE kind='CALLS'")[0]["target_qualified"] == "a.py::work"
        build(incremental, added, full=False)
        build(full, first | added)
        query = "SELECT kind, source_qualified, target_qualified FROM edges ORDER BY kind, source_qualified, target_qualified"
        assert incremental.rows(query) == full.rows(query)
        assert incremental.rows("SELECT target_qualified FROM edges WHERE kind='CALLS'") == [{"target_qualified": "work"}]


def test_flow_excludes_tests_and_singletons_and_preserves_decorated_entry(tmp_path):
    files = {"app.py": '''def helper():
    return 42

@app.get("/health")
def health():
    return helper()

def test_health():
    return health()

def unused():
    pass
'''}
    with GraphStore(tmp_path / "graph.sqlite3") as store:
        build(store, files)
        flows = store.rows("SELECT name, depth, node_count, criticality FROM flows")
        assert [row["name"] for row in flows] == ["health"]
        assert flows[0]["node_count"] == 2
        assert 0 <= flows[0]["criticality"] <= 1


def test_named_javascript_arrow_and_doc_comment_are_indexed():
    parsed = CodeParser().parse_bytes(b'''/** Checkout is idempotent per customer. */
export const checkout = (customerId) => {
    return persist(customerId);
};
function persist(id) { return id; }
''', relative_path="services/orders.js")
    nodes = {node.name: node for node in parsed.nodes}
    assert "checkout" in nodes
    assert "customerId" in nodes["checkout"].extra["params"]
    assert "idempotent per customer" in nodes["checkout"].extra["docstring"]
    assert any(e.source_qualified == nodes["checkout"].qualified_name and e.target_qualified == "persist" for e in parsed.edges)


def test_relative_import_does_not_bind_wrong_top_level_module(tmp_path):
    files = {
        "target.py": "def work():\n    pass\n",
        "pkg/target.py": "def work():\n    pass\n",
        "pkg/caller.py": "from .target import work as execute\ndef run():\n    execute()\n",
    }
    with GraphStore(tmp_path / "graph.sqlite3") as store:
        build(store, files)
        assert store.rows("SELECT target_qualified FROM edges WHERE kind='CALLS'") == [{"target_qualified": "pkg/target.py::work"}]


def test_weighted_leiden_is_deterministic_and_test_follows_production():
    import networkx as nx
    from sage.legion_memory.communities import community_groups

    graph = nx.DiGraph()
    for name in ("checkout", "persist", "stock", "other", "test_checkout"):
        graph.add_node(name, file_path=name + ".py", is_test=name.startswith("test"))
    graph.add_edge("checkout", "persist", kind="CALLS", weight=1)
    graph.add_edge("checkout", "stock", kind="CALLS", weight=1)
    graph.add_edge("checkout", "test_checkout", kind="TESTED_BY", weight=0.4)
    first = community_groups(graph)
    assert first == community_groups(graph)
    assert next(group for group in first if "checkout" in group) >= {"checkout", "test_checkout"}
