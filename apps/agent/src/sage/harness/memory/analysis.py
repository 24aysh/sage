"""Bounded read-only diagnostics"""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import PurePosixPath
import re

from sage.errors import LegionMemoryQueryError
from sage.harness.memory.queries import symbol_candidates
from sage.harness.memory.store import GraphStore, _public_node


def large_nodes(store: GraphStore, min_lines: int, kind: str | None, pattern: str, limit: int) -> dict:
    where = "line_end-line_start+1>=? AND (? IS NULL OR kind=?) AND instr(file_path,?)>0"
    values = (min_lines, kind, kind, pattern)
    total = store.rows(f"SELECT count(*) AS n FROM nodes WHERE {where}", values)[0]["n"]
    rows = store.rows(f"SELECT *, line_end-line_start+1 AS line_count FROM nodes WHERE {where} "
                      "ORDER BY line_count DESC, qualified_name LIMIT ?", (*values, limit))
    return dict(summary=f"Found {total} oversized node(s).", total=total, returned=len(rows),
                data={"nodes": [{**_public_node(r), "line_count": r["line_count"]} for r in rows]})


def surprising_connections(store: GraphStore) -> list[dict]:
    nodes = {r["qualified_name"]: r for r in store.rows("SELECT * FROM nodes WHERE kind!='File'")}
    edges = store.rows("SELECT kind, source_qualified, target_qualified FROM edges ORDER BY id")
    communities = {r["qualified_name"]: r["community_id"] for r in store.rows("SELECT * FROM node_communities")}
    degree = Counter(qn for e in edges for qn in (e["source_qualified"], e["target_qualified"]))
    degrees = sorted(degree.values())
    high = max(degrees[len(degrees) // 2] * 3, 10) if degrees else 10
    results = []
    for edge in edges:
        source, target = nodes.get(edge["source_qualified"]), nodes.get(edge["target_qualified"])
        if not source or not target:
            continue
        a, b = source["qualified_name"], target["qualified_name"]
        checks = (
            ("cross-community", .3, a in communities and b in communities and communities[a] != communities[b]),
            ("cross-language", .2, PurePosixPath(source["file_path"]).suffix != PurePosixPath(target["file_path"]).suffix),
            ("peripheral-to-hub", .2, min(degree[a], degree[b]) <= 2 and max(degree[a], degree[b]) >= high),
            ("cross-test-boundary", .15, source["is_test"] != target["is_test"] and edge["kind"] == "CALLS"),
            ("unusual-edge-kind", .15, source["kind"] == "Type" and edge["kind"] == "CALLS"),
        )
        reasons = [label for label, _, present in checks if present]
        if reasons:
            results.append({**edge, "source": source["name"], "target": target["name"],
                "surprise_score": round(sum(weight for _, weight, present in checks if present), 2),
                "reasons": reasons, "source_community": communities.get(a), "target_community": communities.get(b)})
    return sorted(results, key=lambda r: (-r["surprise_score"], r["source_qualified"], r["target_qualified"]))


def dead_code(store: GraphStore) -> list[dict]:
    rows = store.rows(
        "SELECT n.* FROM nodes n WHERE kind IN ('Function','Class','Type') AND is_test=0 "
        "AND NOT EXISTS (SELECT 1 FROM edges e WHERE e.target_qualified=n.qualified_name "
        "AND e.kind IN ('CALLS','REFERENCES','INHERITS','IMPLEMENTS','TRIGGERS')) "
        "AND NOT EXISTS (SELECT 1 FROM edges e WHERE e.source_qualified=n.qualified_name AND e.kind='HANDLES') "
        "ORDER BY n.qualified_name"
    )
    return [_public_node(r) for r in rows if r["name"] not in {"main", "__init__", "constructor"}]


def knowledge_gaps(store: GraphStore, limit: int) -> dict[str, object]:
    nodes = store.rows("SELECT * FROM nodes WHERE kind!='File' ORDER BY qualified_name")
    edges = store.rows("SELECT kind,source_qualified,target_qualified FROM edges")
    degree = Counter(qn for e in edges for qn in (e["source_qualified"], e["target_qualified"]))
    tested = {e["source_qualified"] for e in edges if e["kind"] == "TESTED_BY"}
    isolated = [{**_public_node(n), "degree": degree[n["qualified_name"]]} for n in nodes if degree[n["qualified_name"]] <= 1]
    hotspots = [{**_public_node(n), "degree": degree[n["qualified_name"]]} for n in nodes
                if degree[n["qualified_name"]] >= 5 and not n["is_test"] and n["qualified_name"] not in tested]
    hotspots.sort(key=lambda n: (-n["degree"], n["qualified_name"]))
    files: dict[int, set[str]] = defaultdict(set)
    for row in store.rows("SELECT m.community_id,n.file_path FROM node_communities m JOIN nodes n ON m.qualified_name=n.qualified_name"):
        files[row["community_id"]].add(row["file_path"])
    communities = store.rows("SELECT id AS community_id,name,size FROM communities ORDER BY id")
    groups = {
        "isolated_nodes": isolated, "untested_hotspots": hotspots,
        "thin_communities": [c for c in communities if c["size"] < 3],
        "single_file_communities": [dict(c, file_path=next(iter(files[c["community_id"]]))) for c in communities
                                    if c["size"] >= 3 and len(files[c["community_id"]]) == 1],
    }
    return {**{name: rows[:limit] for name, rows in groups.items()},
            **{name + "_total": len(rows) for name, rows in groups.items()}}


def suggested_questions(store: GraphStore) -> list[dict]:
    graph = store.graph()
    tested = {r["source_qualified"] for r in store.rows("SELECT source_qualified FROM edges WHERE kind='TESTED_BY'")}
    groups = {r["qualified_name"]: r["community_id"] for r in store.rows("SELECT * FROM node_communities")}
    questions = []
    bridges = sorted(graph, key=lambda n: (-len({groups.get(k) for k in set(graph.predecessors(n)) | set(graph.successors(n))}), n))
    for name in bridges[:3]:
        neighbors = {groups.get(k) for k in set(graph.predecessors(name)) | set(graph.successors(name))}
        if len(neighbors - {None}) > 1:
            questions.append(dict(category="bridge_node", target=name, priority="high",
                question=f"Is connector {name} adequately tested and documented?"))
    for name in sorted(graph, key=lambda n: (-graph.degree(n), n))[:3]:
        if name not in tested and graph.degree(name):
            questions.append(dict(category="hub_risk", target=name, priority="high",
                question=f"Does {name}, with {graph.degree(name)} connections, need direct tests?"))
    for row in surprising_connections(store)[:3]:
        if "cross-community" in row["reasons"]:
            questions.append(dict(category="surprising_connection", target=row["source_qualified"], priority="medium",
                question=f"Is the coupling to {row['target_qualified']} intentional?"))
    for row in store.rows("SELECT id, name FROM communities WHERE size<=2 ORDER BY size,id LIMIT 2"):
        questions.append(dict(category="thin_community", target=f"community:{row['id']}", priority="low",
            question=f"Should small community {row['name']} be merged with a neighbor?"))
    for row in dead_code(store)[:2]:
        questions.append(dict(category="isolated_node", target=row["qualified_name"], priority="medium",
            question="Is this unreferenced symbol dynamically invoked, public API, or dead code?"))
    return questions


def refactor_preview(store: GraphStore, mode: str, old_name: str | None, new_name: str | None, limit: int) -> dict:
    if mode == "dead_code":
        rows = dead_code(store)
    elif mode == "suggest":
        rows = [dict(type="split_community", community_id=r["id"],
                     description=f"Inspect cohesion and boundaries of {r['name']}.")
                for r in store.rows("SELECT * FROM communities WHERE size>20 AND cohesion<0.3 ORDER BY cohesion,id")]
        rows += [dict(type="extract_function", qualified_name=r["qualified_name"],
                      description="Inspect this large function for cohesive extraction opportunities.")
                 for r in store.rows("SELECT qualified_name FROM nodes WHERE kind='Function' AND line_end-line_start>=50 ORDER BY qualified_name")]
    elif mode == "rename":
        if not old_name or not new_name or not re.fullmatch(r"[A-Za-z_$][\w$]*", new_name):
            raise LegionMemoryQueryError("Rename preview requires a symbol and a valid new identifier.")
        candidates = symbol_candidates(store, old_name)
        if len(candidates) != 1:
            return dict(status="ambiguous" if candidates else "not_found",
                summary="Use an exact qualified_name for rename preview.", total=len(candidates),
                returned=min(limit, len(candidates)), data={"candidates": [_public_node(n) for n in candidates[:limit]]})
        node = candidates[0]
        rows = [dict(file_path=node["file_path"], line=node["line_start"], kind="declaration")]
        rows += store.rows("SELECT DISTINCT file_path, line, kind FROM edges WHERE target_qualified=? "
                           "AND kind IN ('CALLS','REFERENCES','INHERITS','IMPLEMENTS') ORDER BY file_path,line",
                           (node["qualified_name"],))
        rows = [{**r, "old_name": node["name"], "new_name": new_name} for r in rows]
    else:
        raise LegionMemoryQueryError("Refactor mode must be rename, dead_code, or suggest.")
    return dict(summary=f"Found {len(rows)} {mode} candidate(s); preview only, verify in current source.",
                total=len(rows), returned=min(limit, len(rows)), data={"mode": mode, "results": rows[:limit],
                "limitations": "Static candidates are not proof of dead code or safe edits. Use Sage's plan, edit and verification tools; no changes or apply tokens are created."})
