"""Bounded structural read/search enrichment"""

from __future__ import annotations

from collections.abc import Callable
from sage.errors import RepositoryError
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


def source_snippets(nodes: list[dict], reader: Callable[..., str]) -> list[dict]:
    """Use the existing source-read boundary; never open graph-supplied paths directly."""
    result = []
    remaining = 6000
    for node in nodes[:5]:
        if remaining < 200:
            break
        start = int(node["line_start"])
        try:
            source = reader(path=node["file_path"], start_line=start,
                            end_line=min(start + 49, int(node["line_end"])))
        except RepositoryError:
            source = "Source read unavailable; inspect the current path separately."
        result.append({"path": node["file_path"], "start_line": start,
                       "source": source[:remaining], "truncated": len(source) > remaining})
        remaining -= min(len(source), remaining)
    return result
