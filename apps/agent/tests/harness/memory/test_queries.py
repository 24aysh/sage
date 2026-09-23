"""Graph query counts, ambiguity, shared identities, and deletion reconciliation."""

from sage.harness.memory.queries import EDGE_PATTERNS, query_graph
from sage.harness.memory.store import GraphStore
from .conftest import apply_files


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


def test_transitive_tests_cycles_and_exact_totals(tmp_path):
    with GraphStore(tmp_path / "graph.sqlite3") as store:
        apply_files(store, {"service.py": "def inner():\n    return outer()\ndef outer():\n    return inner()\n",
                            "test_service.py": "from service import outer\ndef test_first():\n    outer()\ndef test_second():\n    outer()\n"})
        result = query_graph(store, "tests_for", "inner", 1)
        assert result["total"] == 2 and result["returned"] == 1


def test_all_query_patterns_bound_results_and_report_ambiguity(tmp_path):
    with GraphStore(tmp_path / "graph.sqlite3") as store:
        apply_files(store, {"a.py": "def same():\n    pass\n", "b.py": "def same():\n    pass\n"})
        for pattern in EDGE_PATTERNS:
            result = query_graph(store, pattern, "same", 1)
            assert result["status"] == "ambiguous"
            assert result["total"] == 2 and result["returned"] == 1
        result = query_graph(store, "file_summary", "a.py", 1)
        assert result["total"] == 2 and result["returned"] == 1
