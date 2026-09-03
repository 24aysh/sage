"""Build, provenance, and bounded query service for Legion Memory.

The graph model, incremental reconciliation, FTS ranking, impact analysis,
flows, and community concepts are adapted from the MIT-licensed
code-review-graph project (copyright 2026 Tirth Kanani).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from collections import deque
from pathlib import Path, PurePosixPath
from time import perf_counter
from typing import Any, Literal

import networkx as nx

from sage.domain.memory import (
    MemoryBuildResult,
    MemoryBuildType,
    MemoryGraphStats,
    MemoryStatus,
    MemoryToolResult,
)
from sage.errors import LegionMemoryBuildError, LegionMemoryQueryError
from sage.legion_memory.parsing import (
    PARSER_VERSION,
    CodeParser,
    ParsedFile,
    detect_language,
    normalize_path,
)
from sage.legion_memory.store import GraphStore, SCHEMA_VERSION

_IGNORED_PARTS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "__pycache__",
        "node_modules",
        "vendor",
        "dist",
        "build",
        "target",
        ".next",
        ".cache",
        ".sage",
        ".legion-memory",
    }
)
_EDGE_PATTERNS: dict[str, tuple[str, Literal["incoming", "outgoing"]]] = {
    "callers_of": ("CALLS", "incoming"),
    "callees_of": ("CALLS", "outgoing"),
    "references_to": ("REFERENCES", "incoming"),
    "imports_of": ("IMPORTS_FROM", "outgoing"),
    "importers_of": ("IMPORTS_FROM", "incoming"),
    "children_of": ("CONTAINS", "outgoing"),
    "tests_for": ("TESTED_BY", "outgoing"),
    "inheritors_of": ("INHERITS", "incoming"),
}


class LegionMemoryService:
    """Sage-owned local graph capability with no model or network calls."""

    def __init__(self, *, data_root: Path | None = None) -> None:
        self._data_root = data_root

    def resolve_memory_file(
        self,
        repo_root: Path,
        memory_file: Path | None = None,
    ) -> Path:
        root = self._repository_root(repo_root)
        if memory_file is not None:
            return memory_file.expanduser().resolve()
        repository_id = self.repository_id(root)
        safe_name = "".join(
            character if character.isalnum() or character in "-." else "-"
            for character in root.name
        ).strip("-.") or "repository"
        data_root = self._data_root or _default_data_root()
        return (
            data_root.expanduser().resolve()
            / f"{safe_name}-{repository_id[:12]}"
            / "graph.sqlite3"
        )

    def repository_id(self, repo_root: Path) -> str:
        root = self._repository_root(repo_root)
        identity = self._repository_identity(root)
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()

    def build_or_update_graph_tool(
        self,
        *,
        repo_root: Path,
        memory_file: Path | None = None,
        full_rebuild: bool = False,
    ) -> MemoryBuildResult:
        """Build, update, or confirm one graph through a single entry point."""

        started = perf_counter()
        try:
            root = self._repository_root(repo_root)
            indexed_sha = self._git(root, "rev-parse", "--verify", "HEAD").strip()
            repository_id = self.repository_id(root)
            database = self.resolve_memory_file(root, memory_file)
            inventory = self._inventory(root)
            parser = CodeParser()
            warnings: list[str] = []

            with GraphStore(database) as store:
                existing_id = store.get_metadata("repository_id")
                if existing_id and existing_id != repository_id:
                    raise LegionMemoryBuildError(
                        "The selected memory file belongs to a different repository."
                    )
                stored_hashes = store.file_hashes()
                ready = store.get_metadata("build_state") == "ready"
                compatible = store.get_metadata("parser_version") == PARSER_VERSION
                prior_sha = store.get_metadata("indexed_sha")
                history_compatible = bool(prior_sha) and (
                    prior_sha == indexed_sha
                    or self._git_is_ancestor(root, str(prior_sha), indexed_sha)
                )
                is_full = (
                    full_rebuild
                    or not ready
                    or not compatible
                    or not history_compatible
                    or not stored_hashes
                )
                if is_full:
                    changed = sorted(inventory)
                    removed = sorted(set(stored_hashes) - set(inventory))
                    build_type = MemoryBuildType.FULL
                else:
                    changed = sorted(
                        path
                        for path, file_hash in inventory.items()
                        if stored_hashes.get(path) != file_hash
                    )
                    removed = sorted(set(stored_hashes) - set(inventory))
                    build_type = MemoryBuildType.INCREMENTAL

                if not is_full and not changed and not removed:
                    store.update_provenance(
                        indexed_sha=indexed_sha,
                        build_type=MemoryBuildType.NO_CHANGE.value,
                    )
                    stats = store.stats()
                    return self._build_result(
                        stats,
                        build_type=MemoryBuildType.NO_CHANGE,
                        database=database,
                        repository_id=repository_id,
                        indexed_sha=indexed_sha,
                        files_parsed=0,
                        files_removed=0,
                        warnings=(
                            ()
                            if prior_sha == indexed_sha
                            else ("Git SHA changed without indexed file-content changes.",)
                        ),
                        started=started,
                    )

                parsed: list[ParsedFile] = []
                failed: list[str] = []
                for relative in changed:
                    try:
                        content = self._git_bytes(root, "show", f"HEAD:{relative}")
                        item = parser.parse_bytes(
                            content,
                            relative_path=relative,
                            file_hash=inventory[relative],
                        )
                    except (OSError, UnicodeError, ValueError) as error:
                        failed.append(relative)
                        warnings.append(
                            f"Skipped {relative}: {type(error).__name__}: {str(error)[:200]}"
                        )
                        continue
                    parsed.append(item)
                    warnings.extend(item.warnings)

                store.apply_update(
                    parsed_files=parsed,
                    removed_files=(*removed, *failed),
                    repository_id=repository_id,
                    indexed_sha=indexed_sha,
                    parser_version=PARSER_VERSION,
                    build_type=build_type.value,
                    full_rebuild=is_full,
                )
                stats = store.stats()
                return self._build_result(
                    stats,
                    build_type=build_type,
                    database=database,
                    repository_id=repository_id,
                    indexed_sha=indexed_sha,
                    files_parsed=len(parsed),
                    files_removed=len(set((*removed, *failed))),
                    warnings=tuple(warnings[:100]),
                    started=started,
                )
        except LegionMemoryBuildError:
            raise
        except (OSError, ValueError, sqlite3.Error, subprocess.SubprocessError) as error:
            raise LegionMemoryBuildError(
                f"Unable to build Legion Memory: {type(error).__name__}: {str(error)[:300]}"
            ) from error

    def graph_stats(
        self,
        *,
        repo_root: Path,
        memory_file: Path | None = None,
    ) -> MemoryGraphStats:
        root = self._repository_root(repo_root)
        database = self.resolve_memory_file(root, memory_file)
        if not database.is_file():
            return MemoryGraphStats(status=MemoryStatus.MISSING, memory_file=database)
        try:
            with GraphStore(database, read_only=True) as store:
                self._validate_store(store, root)
                stats = store.stats()
                return MemoryGraphStats(
                    status=MemoryStatus.READY,
                    memory_file=database,
                    repository_id=str(stats["repository_id"]),
                    indexed_sha=str(stats["indexed_sha"]),
                    schema_version=int(stats["schema_version"]),
                    build_type=MemoryBuildType(str(stats["build_type"])),
                    files=int(stats["files"]),
                    nodes=int(stats["nodes"]),
                    edges=int(stats["edges"]),
                    flows=int(stats["flows"]),
                    communities=int(stats["communities"]),
                    languages=tuple(str(item) for item in stats["languages"]),
                    last_updated=str(stats["last_updated"]),
                )
        except (OSError, ValueError, sqlite3.Error) as error:
            raise LegionMemoryQueryError(
                f"Unable to read Legion Memory status: {type(error).__name__}: "
                f"{str(error)[:300]}"
            ) from error

    def list_graph_stats_tool(self, **arguments: object) -> dict[str, object]:
        stats = self.graph_stats(**arguments)  # type: ignore[arg-type]
        return MemoryToolResult(
            status=stats.status.value,
            summary=(
                f"{stats.files} files, {stats.nodes} nodes, {stats.edges} edges, "
                f"{stats.flows} flows, {stats.communities} communities."
            ),
            repository_id=stats.repository_id,
            indexed_sha=stats.indexed_sha,
            last_updated=stats.last_updated,
            total=1,
            returned=1,
            data=stats.model_dump(mode="json"),
        ).model_dump(mode="json")

    def semantic_search_nodes_tool(
        self,
        *,
        query: str,
        repo_root: Path,
        memory_file: Path | None = None,
        kind: str | None = None,
        limit: int = 20,
    ) -> dict[str, object]:
        limit = _bounded_int(limit, "limit", maximum=100)
        if not query.strip():
            raise LegionMemoryQueryError("Search query cannot be empty.")
        with self._ready_store(repo_root, memory_file) as store:
            rows, mode = store.search(query, kind=kind, limit=limit)
            return self._result(
                store,
                summary=f"Found {len(rows)} graph node(s) for {query!r} via {mode}.",
                total=len(rows),
                returned=len(rows),
                search_mode=mode,
                data={"nodes": rows},
            )

    def get_minimal_context_tool(
        self,
        *,
        task: str,
        repo_root: Path,
        memory_file: Path | None = None,
    ) -> dict[str, object]:
        with self._ready_store(repo_root, memory_file) as store:
            nodes, mode = store.search(task, kind=None, limit=5)
            communities = store.rows(
                "SELECT id, name, size, cohesion, dominant_language "
                "FROM communities ORDER BY size DESC, id LIMIT 3"
            )
            flows = store.rows(
                "SELECT id, name, node_count, file_count, criticality "
                "FROM flows ORDER BY criticality DESC, id LIMIT 3"
            )
            stats = store.stats()
            return self._result(
                store,
                summary=(
                    f"{stats['nodes']} nodes and {stats['edges']} edges across "
                    f"{stats['files']} files; {len(nodes)} task match(es)."
                ),
                total=len(nodes),
                returned=len(nodes),
                search_mode=mode,
                data={
                    "key_entities": nodes,
                    "communities": communities,
                    "flows": flows,
                    "next_tools": [
                        "semantic_search_nodes_tool",
                        "query_graph_tool",
                        "get_impact_radius_tool",
                    ],
                },
            )

    def query_graph_tool(
        self,
        *,
        pattern: str,
        target: str,
        repo_root: Path,
        memory_file: Path | None = None,
        max_results: int = 50,
    ) -> dict[str, object]:
        max_results = _bounded_int(max_results, "max_results", maximum=100)
        with self._ready_store(repo_root, memory_file) as store:
            if pattern == "file_summary":
                file_path = _relative_path(target)
                rows = store.rows(
                    "SELECT * FROM nodes WHERE file_path = ? ORDER BY line_start, id LIMIT ?",
                    (file_path, max_results + 1),
                )
                results = [_public_node(row) for row in rows[:max_results]]
                return self._result(
                    store,
                    summary=f"Found {len(rows)} node(s) in {target!r}.",
                    total=len(rows),
                    returned=len(results),
                    data={"pattern": pattern, "target": target, "nodes": results},
                )
            if pattern not in _EDGE_PATTERNS:
                raise LegionMemoryQueryError(
                    "Unknown graph query pattern. Available: "
                    + ", ".join((*_EDGE_PATTERNS, "file_summary"))
                )
            node = store.node(target)
            resolved = str(node["qualified_name"]) if node else target
            edge_kind, direction = _EDGE_PATTERNS[pattern]
            join_key = "source_qualified" if direction == "outgoing" else "target_qualified"
            result_key = "target_qualified" if direction == "outgoing" else "source_qualified"
            rows = store.rows(
                f"""SELECT e.kind AS edge_kind, e.confidence, e.line,
                            n.kind, n.name, n.qualified_name, n.file_path,
                            n.line_start, n.line_end, n.language, n.is_test,
                            n.signature
                     FROM edges e
                     LEFT JOIN nodes n ON n.qualified_name=e.{result_key}
                     WHERE e.kind=? AND e.{join_key}=?
                     ORDER BY e.confidence DESC, e.id LIMIT ?""",  # nosec B608
                (edge_kind, resolved, max_results + 1),
            )
            results = [_public_relation(row, result_key=result_key) for row in rows[:max_results]]
            return self._result(
                store,
                summary=f"Found {len(rows)} result(s) for {pattern}({target!r}).",
                total=len(rows),
                returned=len(results),
                data={
                    "pattern": pattern,
                    "target": target,
                    "resolved_target": resolved,
                    "results": results,
                    "confidence": (
                        "No statically visible relationship was indexed; verify in source."
                        if not rows
                        else "Graph relationships are navigation evidence; verify in source."
                    ),
                },
            )

    def traverse_graph_tool(
        self,
        *,
        target: str,
        repo_root: Path,
        memory_file: Path | None = None,
        direction: Literal["incoming", "outgoing", "both"] = "both",
        max_depth: int = 2,
        max_results: int = 50,
    ) -> dict[str, object]:
        max_depth = _bounded_int(max_depth, "max_depth", maximum=5)
        max_results = _bounded_int(max_results, "max_results", maximum=100)
        with self._ready_store(repo_root, memory_file) as store:
            node = store.node(target)
            if node is None:
                return self._result(
                    store,
                    status="not_found",
                    summary=f"No unambiguous graph node matched {target!r}.",
                    data={"target": target, "nodes": []},
                )
            graph = store.graph()
            start = str(node["qualified_name"])
            visited = _traverse(
                graph,
                start,
                direction=direction,
                max_depth=max_depth,
                max_nodes=max_results + 1,
            )
            ordered = sorted(visited.items(), key=lambda item: (item[1], item[0]))
            returned = ordered[:max_results]
            nodes = [_graph_node(graph, name, distance) for name, distance in returned]
            return self._result(
                store,
                summary=f"Traversed {len(ordered)} node(s) from {target!r}.",
                total=len(ordered),
                returned=len(nodes),
                data={"target": target, "direction": direction, "nodes": nodes},
            )

    def get_impact_radius_tool(
        self,
        *,
        changed_files: list[str],
        repo_root: Path,
        memory_file: Path | None = None,
        max_depth: int = 2,
        max_results: int = 50,
    ) -> dict[str, object]:
        max_depth = _bounded_int(max_depth, "max_depth", maximum=5)
        max_results = _bounded_int(max_results, "max_results", maximum=100)
        normalized = tuple(dict.fromkeys(_relative_path(path) for path in changed_files))
        with self._ready_store(repo_root, memory_file) as store:
            seeds = store.rows(
                "SELECT qualified_name FROM nodes WHERE file_path IN ("
                + ",".join("?" for _ in normalized)
                + ") AND kind != 'File'",
                tuple(normalized),
            ) if normalized else []
            graph = store.graph()
            reverse = graph.reverse(copy=False)
            distances: dict[str, int] = {}
            for row in seeds:
                seed = str(row["qualified_name"])
                for name, distance in nx.single_source_shortest_path_length(
                    reverse, seed, cutoff=max_depth
                ).items():
                    if name == seed:
                        continue
                    distances[name] = min(distance, distances.get(name, distance))
            ordered = sorted(
                distances,
                key=lambda name: (distances[name], -graph.degree(name), name),
            )
            visible = ordered[:max_results]
            nodes = [_graph_node(graph, name, distances[name]) for name in visible]
            files = sorted(
                {
                    str(graph.nodes[name].get("file_path", ""))
                    for name in ordered
                    if str(graph.nodes[name].get("file_path", "")) not in normalized
                }
            )
            return self._result(
                store,
                summary=(
                    f"{len(seeds)} changed node(s) reach {len(ordered)} dependent "
                    f"node(s) within {max_depth} hop(s)."
                ),
                total=len(ordered),
                returned=len(nodes),
                data={
                    "changed_files": list(normalized),
                    "impacted_nodes": nodes,
                    "impacted_files": files[:100],
                    "confidence": (
                        "No indexed nodes matched the files; inspect source directly."
                        if not seeds
                        else "Static impact estimate; verify dynamic behavior and source."
                    ),
                },
            )

    def list_flows_tool(
        self,
        *,
        repo_root: Path,
        memory_file: Path | None = None,
        limit: int = 20,
    ) -> dict[str, object]:
        limit = _bounded_int(limit, "limit", maximum=100)
        with self._ready_store(repo_root, memory_file) as store:
            rows = store.rows(
                "SELECT id, name, entry_qualified, depth, node_count, file_count, "
                "criticality FROM flows ORDER BY criticality DESC, id LIMIT ?",
                (limit + 1,),
            )
            return self._result(
                store,
                summary=f"Found {len(rows)} execution flow(s).",
                total=len(rows),
                returned=min(len(rows), limit),
                data={"flows": rows[:limit]},
            )

    def get_flow_tool(
        self,
        *,
        flow_id: int,
        repo_root: Path,
        memory_file: Path | None = None,
        max_steps: int = 100,
    ) -> dict[str, object]:
        max_steps = _bounded_int(max_steps, "max_steps", maximum=200)
        with self._ready_store(repo_root, memory_file) as store:
            flows = store.rows("SELECT * FROM flows WHERE id=?", (flow_id,))
            if not flows:
                return self._result(
                    store,
                    status="not_found",
                    summary=f"Flow {flow_id} was not found.",
                    data={"flow_id": flow_id, "steps": []},
                )
            steps = store.rows(
                """SELECT fm.position, n.kind, n.name, n.qualified_name,
                          n.file_path, n.line_start, n.line_end, n.language,
                          n.is_test, n.signature
                   FROM flow_memberships fm
                   LEFT JOIN nodes n ON n.qualified_name=fm.qualified_name
                   WHERE fm.flow_id=? ORDER BY fm.position LIMIT ?""",
                (flow_id, max_steps + 1),
            )
            visible = [
                _public_node(row) | {"position": row["position"]}
                for row in steps[:max_steps]
            ]
            flow = dict(flows[0])
            flow.pop("path_json", None)
            return self._result(
                store,
                summary=f"Flow {flow_id} contains {len(steps)} indexed step(s).",
                total=len(steps),
                returned=len(visible),
                data={"flow": flow, "steps": visible},
            )

    def get_affected_flows_tool(
        self,
        *,
        changed_files: list[str],
        repo_root: Path,
        memory_file: Path | None = None,
        max_flows: int = 25,
    ) -> dict[str, object]:
        max_flows = _bounded_int(max_flows, "max_flows", maximum=100)
        paths = tuple(dict.fromkeys(_relative_path(path) for path in changed_files))
        with self._ready_store(repo_root, memory_file) as store:
            if not paths:
                rows: list[dict[str, object]] = []
            else:
                marks = ",".join("?" for _ in paths)
                rows = store.rows(
                    f"""SELECT DISTINCT f.id, f.name, f.entry_qualified, f.depth,
                                f.node_count, f.file_count, f.criticality
                         FROM flows f
                         JOIN flow_memberships fm ON fm.flow_id=f.id
                         JOIN nodes n ON n.qualified_name=fm.qualified_name
                         WHERE n.file_path IN ({marks})
                         ORDER BY f.criticality DESC, f.id LIMIT ?""",  # nosec B608
                    (*paths, max_flows + 1),
                )
            return self._result(
                store,
                summary=f"Found {len(rows)} flow(s) affected by {len(paths)} file(s).",
                total=len(rows),
                returned=min(len(rows), max_flows),
                data={"changed_files": list(paths), "flows": rows[:max_flows]},
            )

    def list_communities_tool(
        self,
        *,
        repo_root: Path,
        memory_file: Path | None = None,
        limit: int = 20,
        max_members: int = 10,
    ) -> dict[str, object]:
        limit = _bounded_int(limit, "limit", maximum=100)
        max_members = _bounded_int(max_members, "max_members", maximum=50)
        with self._ready_store(repo_root, memory_file) as store:
            rows = store.rows(
                "SELECT * FROM communities ORDER BY size DESC, id LIMIT ?",
                (limit + 1,),
            )
            visible = [_community(row, max_members=max_members) for row in rows[:limit]]
            return self._result(
                store,
                summary=f"Found {len(rows)} code communities.",
                total=len(rows),
                returned=len(visible),
                data={"communities": visible},
            )

    def get_community_tool(
        self,
        *,
        community_id: int,
        repo_root: Path,
        memory_file: Path | None = None,
        max_members: int = 50,
    ) -> dict[str, object]:
        max_members = _bounded_int(max_members, "max_members", maximum=100)
        with self._ready_store(repo_root, memory_file) as store:
            rows = store.rows("SELECT * FROM communities WHERE id=?", (community_id,))
            if not rows:
                return self._result(
                    store,
                    status="not_found",
                    summary=f"Community {community_id} was not found.",
                    data={"community_id": community_id},
                )
            community = _community(rows[0], max_members=max_members)
            total = int(community["size"])
            returned = len(community["members"])
            return self._result(
                store,
                summary=f"Community {community_id} contains {total} node(s).",
                total=total,
                returned=returned,
                data={"community": community},
            )

    def get_architecture_overview_tool(
        self,
        *,
        repo_root: Path,
        memory_file: Path | None = None,
        max_communities: int = 10,
    ) -> dict[str, object]:
        max_communities = _bounded_int(
            max_communities, "max_communities", maximum=50
        )
        with self._ready_store(repo_root, memory_file) as store:
            communities = store.rows(
                "SELECT * FROM communities ORDER BY size DESC, id LIMIT ?",
                (max_communities + 1,),
            )
            visible = [_community(row, max_members=5) for row in communities[:max_communities]]
            cross_edges = store.rows(
                """SELECT sc.name AS source, tc.name AS target, count(*) AS edges
                   FROM edges e
                   JOIN node_communities sn ON sn.qualified_name=e.source_qualified
                   JOIN node_communities tn ON tn.qualified_name=e.target_qualified
                   JOIN communities sc ON sc.id=sn.community_id
                   JOIN communities tc ON tc.id=tn.community_id
                   WHERE sn.community_id != tn.community_id
                   GROUP BY sc.name, tc.name ORDER BY edges DESC, source, target
                   LIMIT 50"""
            )
            return self._result(
                store,
                summary=(
                    f"Architecture contains {len(communities)} visible communities "
                    f"and {len(cross_edges)} cross-community relationships."
                ),
                total=len(communities),
                returned=len(visible),
                data={"communities": visible, "cross_community_edges": cross_edges},
            )

    def get_hub_nodes_tool(
        self,
        *,
        repo_root: Path,
        memory_file: Path | None = None,
        top_n: int = 10,
    ) -> dict[str, object]:
        return self._rank_graph_nodes(
            repo_root=repo_root,
            memory_file=memory_file,
            top_n=top_n,
            metric="degree",
        )

    def get_bridge_nodes_tool(
        self,
        *,
        repo_root: Path,
        memory_file: Path | None = None,
        top_n: int = 10,
    ) -> dict[str, object]:
        return self._rank_graph_nodes(
            repo_root=repo_root,
            memory_file=memory_file,
            top_n=top_n,
            metric="betweenness",
        )

    def get_knowledge_gaps_tool(
        self,
        *,
        repo_root: Path,
        memory_file: Path | None = None,
        max_results: int = 25,
    ) -> dict[str, object]:
        max_results = _bounded_int(max_results, "max_results", maximum=100)
        with self._ready_store(repo_root, memory_file) as store:
            rows = store.rows(
                """SELECT n.kind, n.name, n.qualified_name, n.file_path,
                          n.line_start, n.line_end, n.language, n.is_test,
                          n.signature,
                          count(DISTINCT incoming.id) AS caller_count
                   FROM nodes n
                   LEFT JOIN edges incoming
                     ON incoming.target_qualified=n.qualified_name
                    AND incoming.kind='CALLS'
                   LEFT JOIN edges tested
                     ON tested.source_qualified=n.qualified_name
                    AND tested.kind='TESTED_BY'
                   WHERE n.kind='Function' AND n.is_test=0 AND tested.id IS NULL
                   GROUP BY n.id
                   ORDER BY caller_count DESC, n.file_path, n.line_start LIMIT ?""",
                (max_results + 1,),
            )
            visible = [
                _public_node(row) | {"caller_count": int(row["caller_count"])}
                for row in rows[:max_results]
            ]
            return self._result(
                store,
                summary=f"Found {len(rows)} untested function hotspot(s).",
                total=len(rows),
                returned=len(visible),
                data={"untested_functions": visible},
            )

    def _rank_graph_nodes(
        self,
        *,
        repo_root: Path,
        memory_file: Path | None,
        top_n: int,
        metric: Literal["degree", "betweenness"],
    ) -> dict[str, object]:
        top_n = _bounded_int(top_n, "top_n", maximum=100)
        with self._ready_store(repo_root, memory_file) as store:
            graph = store.graph()
            if metric == "degree":
                scores = {node: float(value) for node, value in graph.degree()}
            elif len(graph) <= 500:
                scores = nx.betweenness_centrality(graph)
            else:
                scores = nx.betweenness_centrality(graph, k=100, seed=42)
            ordered = sorted(scores, key=lambda node: (-scores[node], node))
            visible = ordered[:top_n]
            nodes = [
                _graph_node(graph, node, 0) | {metric: round(scores[node], 6)}
                for node in visible
            ]
            return self._result(
                store,
                summary=f"Ranked {len(ordered)} node(s) by {metric}.",
                total=len(ordered),
                returned=len(nodes),
                data={f"{metric}_nodes": nodes},
            )

    def _inventory(self, root: Path) -> dict[str, str]:
        output = self._git_bytes(root, "ls-tree", "-r", "-z", "HEAD")
        inventory: dict[str, str] = {}
        for entry in output.split(b"\0"):
            if not entry or b"\t" not in entry:
                continue
            metadata, raw_path = entry.split(b"\t", 1)
            fields = metadata.split()
            if len(fields) != 3 or fields[1] != b"blob":
                continue
            relative = normalize_path(
                raw_path.decode("utf-8", errors="surrogateescape")
            )
            path = PurePosixPath(relative)
            if set(part.casefold() for part in path.parts) & _IGNORED_PARTS:
                continue
            if detect_language(relative) is None:
                continue
            inventory[relative] = fields[2].decode("ascii")
        return inventory

    def _repository_root(self, requested: Path) -> Path:
        path = requested.expanduser().resolve()
        if not path.is_dir():
            raise LegionMemoryBuildError(f"Repository path does not exist: {path}")
        try:
            result = self._git(path, "rev-parse", "--show-toplevel").strip()
        except subprocess.SubprocessError as error:
            raise LegionMemoryBuildError(f"Path is not a Git repository: {path}") from error
        return Path(result).resolve()

    def _repository_identity(self, root: Path) -> str:
        origin = self._git_optional(root, "config", "--get", "remote.origin.url")
        if origin:
            candidate = Path(origin).expanduser()
            if candidate.exists():
                nested = self._git_optional(
                    candidate.resolve(), "config", "--get", "remote.origin.url"
                )
                if nested:
                    origin = nested
        return f"git:{origin.strip()}" if origin else f"path:{root}"

    @staticmethod
    def _git(root: Path, *arguments: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "Git command failed."
            raise subprocess.CalledProcessError(
                result.returncode,
                result.args,
                output=result.stdout,
                stderr=detail,
            )
        return result.stdout

    @staticmethod
    def _git_bytes(root: Path, *arguments: str) -> bytes:
        result = subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=False,
            capture_output=True,
            timeout=60,
        )
        if result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, result.args)
        return result.stdout

    def _git_optional(self, root: Path, *arguments: str) -> str:
        try:
            return self._git(root, *arguments).strip()
        except (OSError, subprocess.SubprocessError):
            return ""

    @staticmethod
    def _git_is_ancestor(root: Path, older: str, newer: str) -> bool:
        result = subprocess.run(
            ["git", "-C", str(root), "merge-base", "--is-ancestor", older, newer],
            check=False,
            capture_output=True,
            timeout=60,
        )
        return result.returncode == 0

    def _build_result(
        self,
        stats: dict[str, object],
        *,
        build_type: MemoryBuildType,
        database: Path,
        repository_id: str,
        indexed_sha: str,
        files_parsed: int,
        files_removed: int,
        warnings: tuple[str, ...],
        started: float,
    ) -> MemoryBuildResult:
        return MemoryBuildResult(
            build_type=build_type,
            memory_file=database,
            repository_id=repository_id,
            indexed_sha=indexed_sha,
            schema_version=SCHEMA_VERSION,
            files_indexed=int(stats["files"]),
            files_parsed=files_parsed,
            files_removed=files_removed,
            total_nodes=int(stats["nodes"]),
            total_edges=int(stats["edges"]),
            total_flows=int(stats["flows"]),
            total_communities=int(stats["communities"]),
            languages=tuple(str(item) for item in stats["languages"]),
            warnings=warnings,
            duration_ms=round((perf_counter() - started) * 1_000, 2),
        )

    def _ready_store(
        self,
        repo_root: Path,
        memory_file: Path | None,
    ) -> GraphStore:
        root = self._repository_root(repo_root)
        database = self.resolve_memory_file(root, memory_file)
        store: GraphStore | None = None
        try:
            store = GraphStore(database, read_only=True)
            self._validate_store(store, root)
            return store
        except (OSError, ValueError, sqlite3.Error) as error:
            if store is not None:
                store.close()
            raise LegionMemoryQueryError(
                f"Unable to query Legion Memory: {type(error).__name__}: {str(error)[:300]}"
            ) from error

    def _validate_store(self, store: GraphStore, root: Path) -> None:
        if store.get_metadata("build_state") != "ready":
            raise ValueError("The graph has no completed ready build.")
        expected = self.repository_id(root)
        if store.get_metadata("repository_id") != expected:
            raise ValueError("The graph belongs to a different repository.")
        current_sha = self._git(root, "rev-parse", "--verify", "HEAD").strip()
        if store.get_metadata("indexed_sha") != current_sha:
            raise ValueError("The graph does not match the repository's current Git SHA.")

    @staticmethod
    def _result(
        store: GraphStore,
        *,
        summary: str,
        total: int = 0,
        returned: int = 0,
        status: str = "ok",
        search_mode: str | None = None,
        data: dict[str, object] | None = None,
    ) -> dict[str, object]:
        result = MemoryToolResult(
            status=status,
            summary=summary,
            repository_id=store.get_metadata("repository_id"),
            indexed_sha=store.get_metadata("indexed_sha"),
            last_updated=store.get_metadata("last_updated"),
            search_mode=search_mode,
            total=total,
            returned=returned,
            omitted=max(0, total - returned),
            truncated=total > returned,
            data=data or {},
        )
        return result.model_dump(mode="json")


def _bounded_int(value: int, name: str, *, maximum: int) -> int:
    if isinstance(value, bool) or value < 1 or value > maximum:
        raise LegionMemoryQueryError(f"{name} must be between 1 and {maximum}.")
    return value


def _default_data_root() -> Path:
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "apps" / "agent" / "pyproject.toml").is_file():
            return candidate / ".sage" / "legion-memory"
    return Path.home() / ".local" / "state" / "sage" / "legion-memory"


def _relative_path(value: str) -> str:
    normalized = normalize_path(value)
    path = PurePosixPath(normalized)
    if not normalized or normalized == "." or path.is_absolute() or ".." in path.parts:
        raise LegionMemoryQueryError(
            "Graph file paths must be repository-relative and cannot contain '..'."
        )
    return normalized


def _public_node(row: dict[str, object]) -> dict[str, object]:
    return {
        key: row.get(key)
        for key in (
            "kind",
            "name",
            "qualified_name",
            "file_path",
            "line_start",
            "line_end",
            "language",
            "is_test",
            "signature",
        )
    }


def _public_relation(
    row: dict[str, object],
    *,
    result_key: str,
) -> dict[str, object]:
    result = _public_node(row)
    if result["qualified_name"] is None:
        result["qualified_name"] = row.get(result_key)
        result["name"] = row.get(result_key)
    result.update(
        {
            "edge_kind": row["edge_kind"],
            "confidence": row["confidence"],
            "edge_line": row["line"],
        }
    )
    return result


def _graph_node(graph: nx.DiGraph, name: str, distance: int) -> dict[str, object]:
    data = graph.nodes[name]
    return {
        "qualified_name": name,
        "name": data.get("name", name),
        "kind": data.get("kind", ""),
        "file_path": data.get("file_path", ""),
        "language": data.get("language", ""),
        "is_test": bool(data.get("is_test", False)),
        "distance": distance,
    }


def _traverse(
    graph: nx.DiGraph,
    start: str,
    *,
    direction: str,
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
        if direction == "incoming":
            neighbors = graph.predecessors(current)
        elif direction == "outgoing":
            neighbors = graph.successors(current)
        else:
            neighbors = (*graph.predecessors(current), *graph.successors(current))
        for neighbor in sorted(neighbors):
            if neighbor not in visited:
                visited[neighbor] = depth + 1
                queue.append(neighbor)
                if len(visited) >= max_nodes:
                    break
    visited.pop(start, None)
    return visited


def _community(row: dict[str, object], *, max_members: int) -> dict[str, object]:
    try:
        raw = str(row["members_json"])
        decoded = json.loads(raw) if len(raw) <= 8_000 else []
        members = [str(item) for item in decoded] if isinstance(decoded, list) else []
    except (KeyError, TypeError, json.JSONDecodeError):
        members = []
    return {
        "id": row["id"],
        "name": row["name"],
        "size": row["size"],
        "cohesion": row["cohesion"],
        "dominant_language": row["dominant_language"],
        "members": members[:max_members],
        "members_omitted": max(0, len(members) - max_members),
    }
