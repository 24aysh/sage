from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from sage.legion_memory.parsing import CodeParser
from sage.legion_memory.store import GraphStore, SCHEMA_VERSION


def _parsed():
    return CodeParser().parse_bytes(
        b"def public_api(value):\n    return value\n",
        relative_path="api.py",
    )


def _populate(store: GraphStore) -> None:
    store.apply_update(
        parsed_files=[_parsed()],
        removed_files=[],
        repository_id="repository-id",
        indexed_sha="a" * 40,
        parser_version="parser-v1",
        build_type="full",
        full_rebuild=True,
    )


def test_store_creates_schema_wal_indexes_and_search(tmp_path: Path) -> None:
    database = tmp_path / "graph.sqlite3"
    with GraphStore(database) as store:
        _populate(store)
        tables = {
            row[0]
            for row in store.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        journal = store.connection.execute("PRAGMA journal_mode").fetchone()[0]
        version = store.connection.execute("PRAGMA user_version").fetchone()[0]
        results, mode = store.search("public_api", kind="Function", limit=5)

    assert {"metadata", "files", "nodes", "edges", "nodes_fts"} <= tables
    assert journal == "wal"
    assert version == SCHEMA_VERSION
    assert mode == "fts"
    assert results[0]["name"] == "public_api"


def test_transaction_rolls_back_and_read_only_store_rejects_writes(
    tmp_path: Path,
) -> None:
    database = tmp_path / "graph.sqlite3"
    with GraphStore(database) as store:
        with pytest.raises(RuntimeError, match="stop"):
            with store.transaction():
                store.set_metadata("temporary", "value")
                raise RuntimeError("stop")
        assert store.get_metadata("temporary") is None

    with GraphStore(database, read_only=True) as store:
        with pytest.raises(ValueError, match="read-only"):
            with store.transaction():
                pass


def test_store_allows_a_concurrent_read_of_a_ready_graph(tmp_path: Path) -> None:
    database = tmp_path / "graph.sqlite3"
    with GraphStore(database) as writer:
        _populate(writer)
        with GraphStore(database, read_only=True) as reader:
            assert reader.stats()["nodes"] == 2


def test_store_rejects_unsupported_or_corrupt_databases(tmp_path: Path) -> None:
    unsupported = tmp_path / "unsupported.sqlite3"
    with GraphStore(unsupported) as store:
        store.set_metadata("schema_version", str(SCHEMA_VERSION + 1))
        store.connection.commit()
    with pytest.raises(ValueError, match="Unsupported"):
        GraphStore(unsupported, read_only=True)

    corrupt = tmp_path / "corrupt.sqlite3"
    corrupt.write_bytes(b"not sqlite")
    with pytest.raises(sqlite3.DatabaseError):
        GraphStore(corrupt, read_only=True)


def test_search_parameterizes_adversarial_input_and_honors_limits(
    tmp_path: Path,
) -> None:
    with GraphStore(tmp_path / "graph.sqlite3") as store:
        _populate(store)
        results, mode = store.search("' OR 1=1 --", kind=None, limit=1)

    assert results == []
    assert mode == "none"
