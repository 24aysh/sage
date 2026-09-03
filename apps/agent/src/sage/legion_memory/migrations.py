"""Ordered SQLite schema migrations for Legion Memory."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class Migration:
    version: int
    sql: str


MIGRATIONS = (
    Migration(
        version=1,
        sql="""
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS files (
    file_path TEXT PRIMARY KEY,
    file_hash TEXT NOT NULL,
    language TEXT NOT NULL,
    indexed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    qualified_name TEXT NOT NULL UNIQUE,
    file_path TEXT NOT NULL,
    line_start INTEGER NOT NULL,
    line_end INTEGER NOT NULL,
    language TEXT NOT NULL,
    parent_qualified TEXT,
    signature TEXT,
    is_test INTEGER NOT NULL DEFAULT 0,
    file_hash TEXT NOT NULL,
    extra_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    source_qualified TEXT NOT NULL,
    target_qualified TEXT NOT NULL,
    file_path TEXT NOT NULL,
    line INTEGER NOT NULL DEFAULT 0,
    confidence REAL NOT NULL DEFAULT 1.0,
    extra_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    UNIQUE(kind, source_qualified, target_qualified, file_path, line)
);
CREATE TABLE IF NOT EXISTS flows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    entry_qualified TEXT NOT NULL UNIQUE,
    depth INTEGER NOT NULL,
    node_count INTEGER NOT NULL,
    file_count INTEGER NOT NULL,
    criticality REAL NOT NULL,
    path_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS flow_memberships (
    flow_id INTEGER NOT NULL REFERENCES flows(id) ON DELETE CASCADE,
    qualified_name TEXT NOT NULL,
    position INTEGER NOT NULL,
    PRIMARY KEY(flow_id, qualified_name)
);
CREATE TABLE IF NOT EXISTS communities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    size INTEGER NOT NULL,
    cohesion REAL NOT NULL,
    dominant_language TEXT NOT NULL,
    members_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS node_communities (
    qualified_name TEXT PRIMARY KEY,
    community_id INTEGER NOT NULL REFERENCES communities(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_nodes_file ON nodes(file_path);
CREATE INDEX IF NOT EXISTS idx_nodes_kind ON nodes(kind);
CREATE INDEX IF NOT EXISTS idx_nodes_name ON nodes(name);
CREATE INDEX IF NOT EXISTS idx_nodes_parent ON nodes(parent_qualified);
CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_qualified);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_qualified);
CREATE INDEX IF NOT EXISTS idx_edges_kind ON edges(kind);
CREATE INDEX IF NOT EXISTS idx_edges_file ON edges(file_path);
CREATE INDEX IF NOT EXISTS idx_flow_memberships_node
    ON flow_memberships(qualified_name);
CREATE INDEX IF NOT EXISTS idx_node_communities_community
    ON node_communities(community_id);
""",
    ),
)


def apply_migrations(connection: sqlite3.Connection) -> None:
    """Apply every pending migration in version order."""

    current = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if current > SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported Legion Memory schema {current}; expected {SCHEMA_VERSION}."
        )
    for migration in MIGRATIONS:
        if migration.version <= current:
            continue
        connection.executescript(migration.sql)
        connection.execute(f"PRAGMA user_version={migration.version}")  # nosec B608
        current = migration.version
