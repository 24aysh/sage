"""Bounded structural read/search enrichment"""

from __future__ import annotations

import json
from sage.legion_memory.store import GraphStore, _public_node


def structural_context(
    store: GraphStore, *, path: str | None = None, query: str | None = None,
    start_line: int = 1, end_line: int | None = None,
) -> list[dict[str, object]]:
    """Return reference-style callers/callees, communities, flows and tests.

    Search enrichment is lexical only: ordinary source reads must not make
    implicit embedding requests. File enrichment respects the requested range.
    """
    if path is not None:
        nodes = store.rows(
            "SELECT * FROM nodes WHERE file_path=? AND kind!='File' "
            "AND line_end>=? AND line_start<=? ORDER BY line_start, qualified_name LIMIT 10",
            (path, start_line, end_line or 2_147_483_647),
        )
    else:
        nodes, _ = store.search(query or "", kind=None, limit=8)
        nodes = [n for n in nodes if not n["is_test"] and n["kind"] != "File"][:5]
    results = []
    for node in nodes:
        qn = node["qualified_name"]
        item = _public_node(node)
        for label, kind, outgoing, limit in (
            ("Called by", "CALLS", False, 5),
            ("Calls", "CALLS", True, 5),
            ("Tests", "TESTED_BY", True, 3),
        ):
            origin, destination = ("source_qualified", "target_qualified") if outgoing else ("target_qualified", "source_qualified")
            rows = store.rows(
                f"SELECT DISTINCT n.qualified_name FROM edges e JOIN nodes n "
                f"ON n.qualified_name=e.{destination} WHERE e.{origin}=? AND e.kind=? "
                "ORDER BY n.qualified_name LIMIT ?", (qn, kind, limit),
            )
            item[label] = [r["qualified_name"] for r in rows]
        item["Flows"] = [r["name"] for r in store.rows(
            "SELECT f.name FROM flows f JOIN flow_memberships m ON m.flow_id=f.id "
            "WHERE m.qualified_name=? ORDER BY f.criticality DESC, f.id LIMIT 3", (qn,),
        )]
        item["Community"] = [r["name"] for r in store.rows(
            "SELECT c.name FROM communities c JOIN node_communities m ON m.community_id=c.id "
            "WHERE m.qualified_name=?", (qn,),
        )]
        results.append(item)
    return results


def render_structural_context(item: dict[str, object]) -> str:
    lines = [f"{item['qualified_name']} ({item['file_path']}:{item['line_start']})"]
    for label in ("Called by", "Calls", "Flows", "Community", "Tests"):
        values = item.get(label)
        if values:
            lines.append(f"  {label}: " + ", ".join(str(v)[:160] for v in values))
    return "\n".join(lines)


def minimal_result(result: dict[str, object]) -> dict[str, object]:
    """Drop repeated node metadata, retaining locators and relationship facts."""
    def project(value: object) -> object:
        if isinstance(value, list):
            return [project(item) for item in value]
        if not isinstance(value, dict):
            return value
        if "qualified_name" in value and "file_path" in value:
            return {key: item for key, item in value.items() if key in {
                "qualified_name", "file_path", "line_start", "line_end", "kind",
                "score", "search_modes", "confidence", "distance", "signature",
                "degree", "betweenness", "caller_count",
                "line_count", "risk_score", "tests",
            }}
        return {key: project(item) for key, item in value.items() if key != "supported_patterns"}
    return {**{key: value for key, value in result.items() if key not in {"repository_id", "last_updated"}},
            "data": project(result.get("data", {}))}


def bounded_json(result: dict[str, object], *, max_chars: int) -> str:
    rendered = json.dumps(result, sort_keys=True, separators=(",", ":"))
    if len(rendered) <= max_chars:
        return rendered
    bounded = json.loads(rendered)
    data = bounded.get("data", {})
    # Keep a useful prefix of results, not a partial JSON node.
    for key in ("results", "nodes", "key_entities", "flows", "communities", "steps", "edges"):
        rows = data.get(key) if isinstance(data, dict) else None
        if not isinstance(rows, list) or not rows:
            continue
        original = len(rows)
        while rows:
            rows.pop()
            bounded.update(truncated=True, returned=max(0, int(result.get("returned", original)) - (original - len(rows))),
                           omitted=int(result.get("omitted", 0)) + original - len(rows))
            rendered = json.dumps(bounded, sort_keys=True, separators=(",", ":"))
            if len(rendered) <= max_chars:
                return rendered
    bounded = {key: value for key, value in result.items() if key not in {"data", "summary"}}
    bounded.update(summary=str(result.get("summary", ""))[:500], returned=0,
        omitted=int(result.get("total", 0) or 0), truncated=True,
        data={"notice": "Result exceeded the native tool character budget; narrow the query."})
    rendered = json.dumps(bounded, sort_keys=True, separators=(",", ":"))
    if len(rendered) <= max_chars:
        return rendered
    return json.dumps({"status": str(result.get("status", "unknown"))[:40], "returned": 0, "truncated": True,
                       "summary": "Metadata exceeds budget; narrow the query."})
