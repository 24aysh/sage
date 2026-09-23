"""SQLite storage for the Legion Memory code graph.
"""

from __future__ import annotations

import json
import posixpath
import re
import sqlite3
from collections import Counter, deque
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import networkx as nx

from sage.harness.memory.migrations import SCHEMA_VERSION, apply_migrations
from sage.harness.memory.parsing import EdgeRecord, NodeRecord, ParsedFile
from sage.harness.memory.communities import community_groups
from sage.harness.memory.resolution import resolve_symbol
from sage.harness.memory.bindings import infer_composition
from sage.harness.memory.tsconfig import config_aliases

_MAX_JSON_CHARS = 8_000


class GraphStore:
    """Own one SQLite graph connection and its bounded query primitives."""

    def __init__(self, path: Path, *, read_only: bool = False) -> None:
        self.path = path.expanduser().resolve()
        self.read_only = read_only
        if read_only:
            if not self.path.is_file():
                raise FileNotFoundError(self.path)
            uri = f"{self.path.as_uri()}?mode=ro"
            self.connection = sqlite3.connect(uri, uri=True, timeout=0.1)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.connection = sqlite3.connect(
                self.path,
                timeout=30,
                isolation_level=None,
            )
        self.connection.row_factory = sqlite3.Row
        self.connection.execute(
            "PRAGMA busy_timeout=100" if read_only else "PRAGMA busy_timeout=5000"
        )
        self.connection.execute("PRAGMA foreign_keys=ON")
        if not read_only:
            self.connection.execute("PRAGMA journal_mode=WAL")
            apply_migrations(self.connection)
            current = self.get_metadata("schema_version")
            if current is None:
                self.set_metadata("schema_version", str(SCHEMA_VERSION))
            elif int(current) != SCHEMA_VERSION:
                raise ValueError(
                    f"Unsupported Legion Memory schema {current}; expected {SCHEMA_VERSION}."
                )
            self.connection.commit()
        self._validate_schema()

    def __enter__(self) -> GraphStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    def _validate_schema(self) -> None:
        required = {"metadata", "files", "nodes", "edges", "flows", "communities"}
        rows = self.connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
        ).fetchall()
        present = {str(row[0]) for row in rows}
        missing = required - present
        if missing:
            raise ValueError(
                "Invalid Legion Memory database; missing tables: "
                + ", ".join(sorted(missing))
            )
        version = self.get_metadata("schema_version")
        if version is None or int(version) != SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported Legion Memory schema {version or 'unknown'}; "
                f"expected {SCHEMA_VERSION}."
            )

    @contextmanager
    def transaction(self) -> Iterator[None]:
        if self.read_only:
            raise ValueError("A read-only graph cannot be mutated.")
        if self.connection.in_transaction:
            self.connection.rollback()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()

    def get_metadata(self, key: str) -> str | None:
        row = self.connection.execute(
            "SELECT value FROM metadata WHERE key = ?", (key,)
        ).fetchone()
        return str(row[0]) if row is not None else None

    def set_metadata(self, key: str, value: str) -> None:
        self.connection.execute(
            "INSERT INTO metadata(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    def file_hashes(self) -> dict[str, str]:
        return {
            str(row["file_path"]): str(row["file_hash"])
            for row in self.connection.execute(
                "SELECT file_path, file_hash FROM files ORDER BY file_path"
            )
        }

    def apply_update(
        self,
        *,
        parsed_files: Iterable[ParsedFile],
        removed_files: Iterable[str],
        repository_id: str,
        indexed_sha: str,
        parser_version: str,
        build_type: str,
        full_rebuild: bool,
    ) -> None:
        parsed = tuple(parsed_files)
        removed = tuple(dict.fromkeys(removed_files))
        now = datetime.now(UTC).isoformat()
        with self.transaction():
            self.set_metadata("build_state", "building")
            if full_rebuild:
                self._clear_graph()
            for file_path in removed:
                self._remove_file(file_path)
            for item in parsed:
                self._replace_file(item, now=now)
            self._resolve_edge_targets()
            self._rebuild_fts()
            self._rebuild_flows()
            self._rebuild_communities()
            languages = [
                str(row[0])
                for row in self.connection.execute(
                    "SELECT DISTINCT language FROM files ORDER BY language"
                )
            ]
            for key, value in (
                ("repository_id", repository_id),
                ("indexed_sha", indexed_sha),
                ("parser_version", parser_version),
                ("last_build_type", build_type),
                ("selected_languages", _json(languages)),
                ("last_updated", now),
                ("build_state", "ready"),
            ):
                self.set_metadata(key, value)

    def update_provenance(
        self,
        *,
        indexed_sha: str,
        build_type: str,
    ) -> None:
        with self.transaction():
            self.set_metadata("indexed_sha", indexed_sha)
            self.set_metadata("last_build_type", build_type)
            self.set_metadata("last_updated", datetime.now(UTC).isoformat())
            self.set_metadata("build_state", "ready")

    def _clear_graph(self) -> None:
        for table in (
            "flow_memberships",
            "flows",
            "node_communities",
            "communities",
            "edges",
            "nodes",
            "files",
        ):
            self.connection.execute(f"DELETE FROM {table}")  # nosec B608
        self.connection.execute(
            "DELETE FROM sqlite_sequence WHERE name IN "
            "('nodes', 'edges', 'flows', 'communities')"
        )

    def _remove_file(self, file_path: str) -> None:
        self.connection.execute("DELETE FROM edges WHERE file_path = ?", (file_path,))
        self.connection.execute("DELETE FROM nodes WHERE file_path = ?", (file_path,))
        self.connection.execute("DELETE FROM files WHERE file_path = ?", (file_path,))

    def _replace_file(self, parsed: ParsedFile, *, now: str) -> None:
        self._remove_file(parsed.file_path)
        self.connection.execute(
            "INSERT INTO files(file_path, file_hash, language, indexed_at) "
            "VALUES (?, ?, ?, ?)",
            (parsed.file_path, parsed.file_hash, parsed.language, now),
        )
        for node in parsed.nodes:
            self._insert_node(node, parsed.file_hash, now)
        for edge in parsed.edges:
            self._insert_edge(edge, now)

    def _insert_node(self, node: NodeRecord, file_hash: str, now: str) -> None:
        self.connection.execute(
            """INSERT INTO nodes(
                kind, name, qualified_name, file_path, line_start, line_end,
                language, parent_qualified, signature, is_test, file_hash,
                extra_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                node.kind,
                node.name,
                node.qualified_name,
                node.file_path,
                node.line_start,
                node.line_end,
                node.language,
                node.parent_qualified,
                node.signature,
                int(node.is_test),
                file_hash,
                _json(node.extra),
                now,
            ),
        )

    def _insert_edge(self, edge: EdgeRecord, now: str) -> None:
        self.connection.execute(
            """INSERT OR REPLACE INTO edges(
                kind, source_qualified, target_qualified, file_path, line,
                confidence, extra_json, updated_at, raw_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                edge.kind,
                edge.source_qualified,
                edge.target_qualified,
                edge.file_path,
                edge.line,
                edge.confidence,
                _json({**edge.extra, "raw_target": edge.target_qualified}),
                now,
                _json((edge.source_qualified, edge.target_qualified, edge.extra.get("receiver", ""))),
            ),
        )

    def _resolve_edge_targets(self) -> None:
        self.connection.execute("DELETE FROM edges WHERE kind='CALLS' AND json_extract(extra_json,'$.event_dispatch')=1")
        self.connection.execute("DELETE FROM nodes WHERE kind IN ('Event','ConfigKey') AND json_extract(extra_json,'$.derived_memory')=1")
        rows = self.connection.execute(
            "SELECT id, kind, source_qualified, target_qualified, file_path, extra_json FROM edges "
            "WHERE kind IN ('CALLS', 'INHERITS', 'IMPLEMENTS', 'IMPORTS_FROM', 'REFERENCES', 'HANDLES')"
        ).fetchall()
        node_names: dict[str, list[sqlite3.Row]] = {}
        for row in self.connection.execute(
            "SELECT qualified_name, name, file_path, kind, parent_qualified, language, is_test, extra_json FROM nodes"
        ):
            node_names.setdefault(str(row["name"]), []).append(row)
        file_paths = {
            str(row["file_path"]): str(row["qualified_name"])
            for row in self.connection.execute(
                "SELECT file_path, qualified_name FROM nodes WHERE kind='File'"
            )
        }
        all_nodes = {str(node["qualified_name"]): dict(node) for group in node_names.values() for node in group}
        by_name = {name: [all_nodes[str(n["qualified_name"])] for n in group] for name, group in node_names.items()}

        def resolve_module(target: str, *, source_path: str, file_paths: dict[str, str]) -> str | None:
            if "::" in target:
                target = target.removeprefix("crate::").replace("::", "/")
            if not target.startswith("."):
                configs = sorted((p for p in file_paths if PurePosixPath(p).name.startswith("tsconfig") and p.endswith((".json", ".jsonc"))),
                                 key=lambda p: (-len(PurePosixPath(p).parts), p))
                for config in configs:
                    directory = PurePosixPath(config).parent
                    if not PurePosixPath(source_path).is_relative_to(directory):
                        continue
                    options = config_aliases(config, all_nodes)
                    matches = []
                    for pattern, replacements in options.get("paths", {}).items():
                        prefix, wildcard, suffix = pattern.partition("*")
                        if (not wildcard and target == pattern) or (wildcard and target.startswith(prefix) and target.endswith(suffix)):
                            middle = target[len(prefix):len(target) - len(suffix) if suffix else None]
                            for replacement in replacements:
                                expanded = posixpath.normpath(replacement.replace("*", middle))
                                candidate = _resolve_import(expanded, source_path="", file_paths=file_paths)
                                if candidate:
                                    matches.append(candidate)
                    if matches:
                        return matches[0] if len(set(matches)) == 1 else None
                    if options.get("baseUrl"):
                        found = _resolve_import(posixpath.normpath(base + "/" + target), source_path="", file_paths=file_paths)
                        if found:
                            return found
            return _resolve_import(target, source_path=source_path, file_paths=file_paths)
        receiver_type = infer_composition(all_nodes, lambda name, source: resolve_symbol(
            name, source=source, nodes=all_nodes, files=file_paths,
            resolve_import=resolve_module, by_name=by_name))
        for row in rows:
            extra = json.loads(row["extra_json"])
            extra.pop("resolution_evidence", None)
            target = str(extra.get("raw_target", row["target_qualified"]))
            resolved: str | None = None
            if row["kind"] == "HANDLES":
                raw_source = str(extra.get("raw_source", row["source_qualified"]))
                source = all_nodes.get(str(row["file_path"]), {})
                resolved_source = resolve_symbol(raw_source, source=source, nodes=all_nodes,
                    files=file_paths, resolve_import=resolve_module, by_name=by_name)
                extra["raw_source"] = raw_source
                self.connection.execute("UPDATE edges SET source_qualified=?, extra_json=? WHERE id=?",
                    (resolved_source or raw_source, _json(extra), row["id"]))
                continue
            if row["kind"] == "IMPORTS_FROM":
                resolved = resolve_module(
                    target,
                    source_path=str(row["file_path"]),
                    file_paths=file_paths,
                )
            else:
                source = all_nodes.get(str(row["source_qualified"]), all_nodes[str(row["file_path"])])
                receiver = str(extra.get("receiver", ""))
                inferred = receiver_type(receiver, str(source["qualified_name"])) if receiver else None
                if inferred:
                    source = dict(source)
                    metadata = json.loads(source["extra_json"])
                    metadata["receivers"] = {**metadata.get("receivers", {}), receiver: inferred}
                    source["extra_json"] = json.dumps(metadata)
                    extra["resolution_evidence"] = "static_composition"
                resolved = resolve_symbol(target, source=source, nodes=all_nodes,
                    files=file_paths, resolve_import=resolve_module,
                    receiver=str(extra.get("receiver", "")), kind=str(row["kind"]), by_name=by_name)
            # Resolve from the original spelling even when an unchanged caller
            # previously pointed at a now-renamed or newly ambiguous symbol.
            self.connection.execute(
                "UPDATE edges SET target_qualified = ?, extra_json=? WHERE id = ?",
                (resolved or target, _json({**extra, "resolved": resolved is not None}), row["id"]),
            )

        self.connection.execute("DELETE FROM edges WHERE kind='TRIGGERS'")
        for event in self.connection.execute("SELECT * FROM edges WHERE kind IN ('HANDLES','PUBLISHES') AND target_qualified LIKE 'event::%'").fetchall():
            source = all_nodes.get(str(event["source_qualified"]))
            if not source or source["language"] != "java":
                continue
            extra = json.loads(event["extra_json"])
            raw = str(extra.get("raw_target", event["target_qualified"])).removeprefix("event::")
            resolved = resolve_symbol(raw, source=source, nodes=all_nodes, files=file_paths, resolve_import=resolve_module, by_name=by_name)
            target = "event::" + (resolved or f"unresolved:{source['file_path']}:{raw}")
            self.connection.execute("UPDATE edges SET target_qualified=? WHERE id=?", (target, event["id"]))
        self.connection.execute(
            "INSERT OR IGNORE INTO edges(kind, source_qualified, target_qualified, file_path, line, confidence, extra_json, updated_at) "
            "SELECT 'TRIGGERS', p.source_qualified, h.source_qualified, p.file_path, p.line, 0.9, '{}', p.updated_at "
            "FROM edges p JOIN edges h ON p.target_qualified=h.target_qualified "
            "JOIN nodes n ON n.qualified_name=h.source_qualified "
            "WHERE p.kind='PUBLISHES' AND h.kind='HANDLES' AND p.target_qualified LIKE 'event::%'"
        )
        self.connection.execute(
            "INSERT OR IGNORE INTO edges(kind,source_qualified,target_qualified,file_path,line,confidence,extra_json,updated_at) "
            "SELECT 'CALLS',source_qualified,target_qualified,file_path,line,confidence,'{\"event_dispatch\":true}',updated_at "
            "FROM edges WHERE kind='TRIGGERS'"
        )
        # Rebuild shared virtual identities globally so deleting their original
        # owner does not leave orphan event/config nodes or stale source locators.
        virtual = self.rows(
            "SELECT target_qualified,file_path,line FROM edges "
            "WHERE (kind IN ('PUBLISHES','HANDLES') AND target_qualified LIKE 'event::%') "
            "OR (kind='CONSUMES' AND target_qualified LIKE 'config:%') "
            "ORDER BY file_path,line,target_qualified"
        )
        seen_virtual: set[str] = set()
        hashes = self.file_hashes()
        for row in virtual:
            target = str(row["target_qualified"])
            owner = all_nodes.get(str(row["file_path"]))
            if target in seen_virtual or owner is None:
                continue
            seen_virtual.add(target)
            name = target.removeprefix("event::").removeprefix("config:")
            kind = "Event" if target.startswith("event::") else "ConfigKey"
            self._insert_node(NodeRecord(kind, name, target, str(row["file_path"]),
                max(1, int(row["line"])), max(1, int(row["line"])), str(owner["language"]),
                str(row["file_path"]), extra={"derived_memory": True}),
                file_hash=hashes[str(row["file_path"])], now=datetime.now(UTC).isoformat())

        self.connection.execute(
            """DELETE FROM edges
               WHERE kind IN ('CALLS', 'INHERITS')
                 AND instr(target_qualified, '::') > 0
                 AND NOT EXISTS (
                     SELECT 1 FROM nodes
                     WHERE nodes.qualified_name=edges.target_qualified
                 )"""
        )

        self.connection.execute(
            "DELETE FROM edges WHERE kind='TESTED_BY'"
        )
        test_calls = self.connection.execute(
            """SELECT e.target_qualified, e.source_qualified, e.file_path, e.line
               FROM edges e
               JOIN nodes source ON source.qualified_name=e.source_qualified
               JOIN nodes target ON target.qualified_name=e.target_qualified
               WHERE e.kind='CALLS' AND source.is_test=1 AND target.is_test=0"""
        ).fetchall()
        now = datetime.now(UTC).isoformat()
        for row in test_calls:
            self.connection.execute(
                """INSERT OR IGNORE INTO edges(
                    kind, source_qualified, target_qualified, file_path, line,
                    confidence, extra_json, updated_at
                ) VALUES ('TESTED_BY', ?, ?, ?, ?, 0.8, '{}', ?)""",
                (
                    row["target_qualified"],
                    row["source_qualified"],
                    row["file_path"],
                    row["line"],
                    now,
                ),
            )

    def _rebuild_fts(self) -> None:
        self.connection.execute("DROP TABLE IF EXISTS nodes_fts")
        self.connection.execute(
            """CREATE VIRTUAL TABLE nodes_fts USING fts5(
                node_id UNINDEXED,
                name,
                qualified_name,
                file_path,
                signature,
                tokenize='porter unicode61'
            )"""
        )
        self.connection.execute(
            """INSERT INTO nodes_fts(node_id, name, qualified_name, file_path, signature)
               SELECT id, name, qualified_name, file_path, COALESCE(signature, '')
               FROM nodes"""
        )

    def _symbol_graph(self) -> nx.DiGraph:
        graph = nx.DiGraph()
        for row in self.connection.execute(
            "SELECT qualified_name, name, kind, file_path, language, is_test, extra_json "
            "FROM nodes WHERE kind != 'File'"
        ):
            graph.add_node(
                str(row["qualified_name"]),
                name=str(row["name"]),
                kind=str(row["kind"]),
                file_path=str(row["file_path"]),
                language=str(row["language"]),
                is_test=bool(row["is_test"]),
                extra=json.loads(row["extra_json"]),
            )
        for row in self.connection.execute(
            "SELECT source_qualified, target_qualified, kind FROM edges "
            "WHERE kind IN ('CALLS', 'INHERITS', 'IMPLEMENTS', 'CONTAINS', 'TESTED_BY', 'HANDLES', 'PUBLISHES', 'CONSUMES')"
        ):
            source = str(row["source_qualified"])
            target = str(row["target_qualified"])
            if source in graph and target in graph:
                weight = {"CALLS": 1.0, "INHERITS": 0.8, "IMPLEMENTS": 0.7, "TESTED_BY": 0.4, "CONTAINS": 0.3}.get(str(row["kind"]), 0.5)
                if not graph.has_edge(source, target) or row["kind"] == "CALLS":
                    graph.add_edge(source, target, kind=str(row["kind"]), weight=weight)
        return graph

    def _rebuild_flows(self) -> None:
        self.connection.execute("DELETE FROM flow_memberships")
        self.connection.execute("DELETE FROM flows")
        self.connection.execute("DELETE FROM sqlite_sequence WHERE name='flows'")
        graph = self._symbol_graph()
        call_graph = nx.DiGraph(
            (source, target)
            for source, target, data in graph.edges(data=True)
            if data.get("kind") == "CALLS"
        )
        for node, data in graph.nodes(data=True):
            if node not in call_graph:
                call_graph.add_node(node)
            call_graph.nodes[node].update(data)
        entries = [
            node
            for node in call_graph
            if not call_graph.nodes[node].get("is_test")
            and call_graph.nodes[node].get("kind") == "Function"
            and (call_graph.in_degree(node) == 0
                 or _entry_name(str(call_graph.nodes[node].get("name", "")))
                 or any(re.search(r"\.(route|get|post|put|patch|delete)\b", str(d)) for d in call_graph.nodes[node].get("extra", {}).get("decorators", [])))
        ]
        entries.sort(key=lambda item: (call_graph.nodes[item].get("file_path", ""), item))
        tested = {str(r[0]) for r in self.connection.execute("SELECT DISTINCT source_qualified FROM edges WHERE kind='TESTED_BY'")}
        external_calls = Counter(str(r[0]) for r in self.connection.execute("SELECT source_qualified, target_qualified FROM edges WHERE kind='CALLS'") if r[1] not in graph)
        for entry in entries[:200]:
            visited = _bounded_bfs(call_graph, entry, max_depth=15, max_nodes=200)
            if len(visited) < 2:
                continue
            files = {
                str(call_graph.nodes[node].get("file_path", "")) for node in visited
            }
            depth = max(visited.values())
            criticality = round(
                min((len(files) - 1) / 4, 1) * 0.30
                + min(sum(external_calls[n] for n in visited) / 5, 1) * 0.20
                + sum(_security_name(n) for n in visited) / len(visited) * 0.25
                + (1 - len(set(visited) & tested) / len(visited)) * 0.15
                + min(depth / 10, 1) * 0.10, 6)
            name = str(call_graph.nodes[entry].get("name", entry))
            cursor = self.connection.execute(
                """INSERT INTO flows(
                    name, entry_qualified, depth, node_count, file_count,
                    criticality, path_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    name,
                    entry,
                    depth,
                    len(visited),
                    len(files),
                    criticality,
                    _json(list(visited)),
                ),
            )
            flow_id = int(cursor.lastrowid)
            for position, member in enumerate(visited):
                self.connection.execute(
                    "INSERT INTO flow_memberships(flow_id, qualified_name, position) "
                    "VALUES (?, ?, ?)",
                    (flow_id, member, position),
                )

    def _rebuild_communities(self) -> None:
        self.connection.execute("DELETE FROM node_communities")
        self.connection.execute("DELETE FROM communities")
        self.connection.execute("DELETE FROM sqlite_sequence WHERE name='communities'")
        directed = self._symbol_graph()
        graph = directed.to_undirected()
        if not graph:
            return
        groups = community_groups(directed)
        ordered = sorted(
            (set(group) for group in groups if len(group) >= 2),
            key=lambda group: (-len(group), min(group)),
        )
        membership = {member: index for index, members in enumerate(ordered) for member in members}
        internal, external = Counter(), Counter()
        for source, target in directed.edges:
            a, b = membership.get(source), membership.get(target)
            if a is not None and a == b:
                internal[a] += 1
            else:
                if a is not None:
                    external[a] += 1
                if b is not None:
                    external[b] += 1
        for index, members in enumerate(ordered, start=1):
            total = internal[index - 1] + external[index - 1]
            cohesion = internal[index - 1] / total if total else 0.0
            languages = Counter(
                str(graph.nodes[node].get("language", "")) for node in members
            )
            paths = Counter(
                PurePosixPath(str(graph.nodes[node].get("file_path", ""))).parts[0]
                for node in members
                if PurePosixPath(str(graph.nodes[node].get("file_path", ""))).parts
            )
            production = sorted(n for n in members if not graph.nodes[n].get("is_test"))
            names = Counter(str(graph.nodes[n].get("name", "")) for n in production if graph.nodes[n].get("kind") == "Class")
            prefix = names.most_common(1)[0][0] if names else paths.most_common(1)[0][0] if paths else "root"
            name = f"{prefix}-{index}"
            sorted_members = sorted(members)
            cursor = self.connection.execute(
                """INSERT INTO communities(
                    name, size, cohesion, dominant_language, members_json
                ) VALUES (?, ?, ?, ?, ?)""",
                (
                    name,
                    len(members),
                    cohesion,
                    languages.most_common(1)[0][0] if languages else "",
                    _json(sorted_members),
                ),
            )
            community_id = int(cursor.lastrowid)
            self.connection.executemany(
                "INSERT INTO node_communities(qualified_name, community_id) "
                "VALUES (?, ?)",
                ((member, community_id) for member in sorted_members),
            )

    def stats(self) -> dict[str, object]:
        def count(table: str) -> int:
            return int(
                self.connection.execute(
                    f"SELECT count(*) FROM {table}"  # nosec B608
                ).fetchone()[0]
            )

        languages = tuple(
            str(row[0])
            for row in self.connection.execute(
                "SELECT DISTINCT language FROM files ORDER BY language"
            )
        )
        return {
            "repository_id": self.get_metadata("repository_id"),
            "indexed_sha": self.get_metadata("indexed_sha"),
            "schema_version": int(self.get_metadata("schema_version") or 0),
            "build_type": self.get_metadata("last_build_type"),
            "last_updated": self.get_metadata("last_updated"),
            "build_state": self.get_metadata("build_state"),
            "files": count("files"),
            "nodes": count("nodes"),
            "edges": count("edges"),
            "flows": count("flows"),
            "communities": count("communities"),
            "languages": languages,
        }

    def rows(self, sql: str, parameters: tuple[object, ...] = ()) -> list[dict[str, object]]:
        return [dict(row) for row in self.connection.execute(sql, parameters)]

    def exact_node(self, target: str) -> dict[str, object] | None:
        """Look up stored identity without resolving an unresolved edge by name."""
        row = self.connection.execute(
            "SELECT * FROM nodes WHERE qualified_name = ?", (target,),
        ).fetchone()
        return _public_node(row) if row is not None else None

    def node(self, target: str) -> dict[str, object] | None:
        row = self.connection.execute(
            "SELECT * FROM nodes WHERE qualified_name = ?", (target,)
        ).fetchone()
        if row is None:
            row = self.connection.execute(
                "SELECT * FROM nodes WHERE file_path = ? AND kind='File'", (target,)
            ).fetchone()
        if row is None:
            rows = self.connection.execute(
                "SELECT * FROM nodes WHERE name = ? ORDER BY is_test, file_path LIMIT 2",
                (target,),
            ).fetchall()
            row = rows[0] if len(rows) == 1 else None
        return _public_node(row) if row is not None else None

    def search(
        self,
        query: str,
        *,
        kind: str | None,
        limit: int,
    ) -> tuple[list[dict[str, object]], str]:
        tokens = [token for token in _search_tokens(query) if token]
        rows: list[sqlite3.Row] = []
        mode = "none"
        if tokens:
            expression = " OR ".join(f'"{token}"' for token in tokens[:12])
            try:
                rows = self.connection.execute(
                    """SELECT n.*, bm25(nodes_fts) AS rank
                       FROM nodes_fts
                       JOIN nodes n ON n.id=nodes_fts.node_id
                       WHERE nodes_fts MATCH ?
                       ORDER BY rank LIMIT ?""",
                    (expression, limit * 3),
                ).fetchall()
                mode = "fts" if rows else "none"
            except sqlite3.OperationalError:
                rows = []
        if not rows and query.strip():
            pattern = f"%{_like_escape(query.strip())}%"
            rows = self.connection.execute(
                """SELECT *, 0.0 AS rank FROM nodes
                   WHERE name LIKE ? ESCAPE '\\'
                      OR qualified_name LIKE ? ESCAPE '\\'
                      OR file_path LIKE ? ESCAPE '\\'
                      OR COALESCE(signature, '') LIKE ? ESCAPE '\\'
                   ORDER BY is_test, file_path, line_start LIMIT ?""",
                (pattern, pattern, pattern, pattern, limit * 3),
            ).fetchall()
            mode = "keyword" if rows else "none"
        results: list[dict[str, object]] = []
        query_folded = query.casefold()
        for row in rows:
            if kind and row["kind"] != kind:
                continue
            item = _public_node(row)
            score = -float(row["rank"] or 0.0)
            if query_folded == str(row["name"]).casefold():
                score += 10.0
            if query_folded in str(row["qualified_name"]).casefold():
                score += 2.0
            item["score"] = round(score, 6)
            results.append(item)
        results.sort(key=lambda item: (-float(item["score"]), str(item["qualified_name"])))
        return results[:limit], mode

    def graph(self) -> nx.DiGraph:
        return self._symbol_graph()


def _json(value: object) -> str:
    rendered = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return rendered if len(rendered) <= _MAX_JSON_CHARS else "{}"


def _public_node(row: sqlite3.Row) -> dict[str, object]:
    return {
        "kind": str(row["kind"]),
        "name": str(row["name"]),
        "qualified_name": str(row["qualified_name"]),
        "file_path": str(row["file_path"]),
        "line_start": int(row["line_start"]),
        "line_end": int(row["line_end"]),
        "language": str(row["language"]),
        "is_test": bool(row["is_test"]),
        "signature": str(row["signature"] or "")[:500],
    }


def _resolve_import(
    target: str,
    *,
    source_path: str,
    file_paths: dict[str, str],
) -> str | None:
    if target.startswith("."):
        parent = str(PurePosixPath(source_path).parent)
        if "/" in target:
            relative = posixpath.normpath(posixpath.join(parent, target))
        else:
            levels = len(target) - len(target.lstrip("."))
            relative = posixpath.normpath(posixpath.join(parent, *(".." for _ in range(levels - 1)), target.lstrip(".").replace(".", "/")))
        matches = [path for path in file_paths if path in {
            relative, *(relative + suffix for suffix in (".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".htm", ".css")),
            relative + "/__init__.py", relative + "/index.js", relative + "/index.ts",
        }]
        return file_paths[matches[0]] if len(matches) == 1 else None
    normalized = target.replace("\\", "/").strip("./")
    source_parent = PurePosixPath(source_path).parent
    candidates = [normalized, str(source_parent / normalized)]
    dotted = normalized.replace(".", "/")
    candidates.extend((dotted, f"{dotted}.py", f"{dotted}/__init__.py"))
    extensions = (".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".html", ".htm", ".css")
    for candidate in tuple(candidates):
        candidates.extend(f"{candidate}{suffix}" for suffix in extensions)
        candidates.extend(f"{candidate}/index{suffix}" for suffix in extensions[1:5])
    for candidate in candidates:
        clean = PurePosixPath(candidate).as_posix()
        if clean in file_paths:
            return file_paths[clean]
        suffix_matches = [qn for path, qn in file_paths.items() if path.endswith("/" + clean)]
        if len(suffix_matches) == 1:
            return suffix_matches[0]
    return None


def _bounded_bfs(
    graph: nx.DiGraph,
    start: str,
    *,
    max_depth: int,
    max_nodes: int,
) -> dict[str, int]:
    visited = {start: 0}
    queue = deque([start])
    while queue and len(visited) < max_nodes:
        current = queue.popleft()
        depth = visited[current]
        if depth >= max_depth:
            continue
        for neighbor in sorted(graph.successors(current)):
            if neighbor not in visited:
                visited[neighbor] = depth + 1
                queue.append(neighbor)
                if len(visited) >= max_nodes:
                    break
    return visited


def _entry_name(name: str) -> bool:
    lowered = name.casefold()
    return lowered in {"main", "handler", "handle", "run"} or lowered.startswith(
        ("handle_", "on_", "test_", "do_")
    )


def _security_name(value: str) -> bool:
    lowered = value.casefold()
    return any(token in lowered for token in ("auth", "security", "permission", "token"))


def _search_tokens(query: str) -> list[str]:
    tokens = []
    for token in re.findall(r"[A-Za-z0-9_./:-]+", query):
        cleaned = token.replace('"', "").strip()
        if len(cleaned) >= 2 and cleaned not in tokens:
            tokens.append(cleaned)
    return tokens


def _like_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
