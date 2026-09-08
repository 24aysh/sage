"""Predefined read-only query patterns"""

from __future__ import annotations

from pathlib import PurePosixPath

from sage.errors import LegionMemoryQueryError
from sage.legion_memory.store import GraphStore, _public_node

EDGE_PATTERNS = {
    "callers_of": (("CALLS",), "incoming"),
    "callees_of": (("CALLS",), "outgoing"),
    "references_to": (("REFERENCES",), "incoming"),
    "imports_of": (("IMPORTS_FROM",), "outgoing"),
    "importers_of": (("IMPORTS_FROM",), "incoming"),
    "children_of": (("CONTAINS",), "outgoing"),
    "tests_for": (("TESTED_BY",), "outgoing"),
    "inheritors_of": (("INHERITS", "IMPLEMENTS"), "incoming"),
    "triggers_of": (("TRIGGERS",), "outgoing"),
    "triggered_by": (("TRIGGERS",), "incoming"),
    "publishers_of": (("PUBLISHES",), "incoming"),
    "listeners_of": (("HANDLES",), "incoming"),
    "handlers_of": (("HANDLES",), "incoming"),
    "endpoints_for": (("HANDLES",), "outgoing"),
    "consumers_of": (("CONSUMES",), "incoming"),
}


def symbol_candidates(store: GraphStore, target: str) -> list[dict[str, object]]:
    exact = store.rows("SELECT * FROM nodes WHERE qualified_name=?", (target,))
    if exact:
        return exact
    return store.rows(
        "SELECT * FROM nodes WHERE name=? OR substr(qualified_name, -?)=? "
        "ORDER BY qualified_name", (target, len("::" + target), "::" + target),
    )


def query_graph(store: GraphStore, pattern: str, target: str, limit: int) -> dict[str, object]:
    if pattern == "file_summary":
        if PurePosixPath(target).is_absolute() or ".." in PurePosixPath(target).parts:
            raise LegionMemoryQueryError("Use a repository-relative file path.")
        total = store.rows("SELECT count(*) AS n FROM nodes WHERE file_path=?", (target,))[0]["n"]
        rows = store.rows("SELECT * FROM nodes WHERE file_path=? ORDER BY line_start, qualified_name LIMIT ?", (target, limit))
        return dict(summary=f"Found {total} node(s) in {target!r}.", total=total,
                    returned=len(rows), data={"pattern": pattern, "target": target,
                                             "nodes": [_public_node(r) for r in rows]})
    if pattern not in EDGE_PATTERNS:
        raise LegionMemoryQueryError("Unknown graph query pattern. Available: " + ", ".join((*EDGE_PATTERNS, "file_summary")))
    candidates = symbol_candidates(store, target)
    if len(candidates) > 1:
        return dict(status="ambiguous", summary="Multiple symbols match; retry with a qualified_name.",
                    total=len(candidates), returned=min(limit, len(candidates)),
                    data={"candidates": [_public_node(r) for r in candidates[:limit]]})
    resolved = str(candidates[0]["qualified_name"]) if candidates else target
    if pattern in {"publishers_of", "listeners_of"} and not resolved.startswith("event::"):
        resolved = "event::" + resolved
    if pattern == "imports_of" and candidates:
        resolved = str(candidates[0]["file_path"])
    if pattern == "tests_for" and candidates:
        from sage.legion_memory.review import transitive_tests

        names = transitive_tests(store, resolved)
        results = [{**_public_node(store.exact_node(name)), "edge_kind": "TESTED_BY"}
                   for name in names[:limit] if store.exact_node(name)]
        return dict(summary=f"Found {len(names)} directly or transitively linked test(s).",
                    total=len(names), returned=len(results), data={"pattern": pattern,
                    "target": target, "resolved_target": resolved, "results": results,
                    "confidence": "Static test links; traversal capped at 15 hops/2000 nodes; verify in source."})
    kinds, direction = EDGE_PATTERNS[pattern]
    origin, destination = ("source_qualified", "target_qualified") if direction == "outgoing" else ("target_qualified", "source_qualified")
    condition = f"e.kind IN ({','.join('?' for _ in kinds)}) AND e.{origin}=?"
    parameters: tuple[object, ...] = (*kinds, resolved)
    if pattern == "consumers_of" and not candidates:
        key = target.removeprefix("config:").removesuffix(".*")
        condition = "e.kind='CONSUMES' AND (e.target_qualified=? OR substr(e.target_qualified,1,?)=?)"
        prefix = "config:" + key + "."
        parameters = ("config:" + key, len(prefix), prefix)
    if pattern == "endpoints_for":
        condition += " AND n.kind='Endpoint'"
    join = f"FROM edges e LEFT JOIN nodes n ON n.qualified_name=e.{destination} WHERE {condition}"
    total = store.rows(f"SELECT count(DISTINCT e.{destination}) AS n {join}", parameters)[0]["n"]
    rows = store.rows(
        f"SELECT n.*, e.{destination} AS locator, e.kind AS edge_kind, "
        f"MAX(e.confidence) AS confidence, MIN(e.line) AS edge_line {join} "
        f"GROUP BY e.{destination} ORDER BY confidence DESC, locator LIMIT ?", (*parameters, limit),
    )
    results = []
    for row in rows:
        item = _public_node(row) if row["id"] is not None else {
            "qualified_name": row["locator"], "name": row["locator"], "resolved": False,
        }
        item.update(edge_kind=row["edge_kind"], confidence=row["confidence"], edge_line=row["edge_line"])
        results.append(item)
    return dict(summary=f"Found {total} result(s) for {pattern}({target!r}).",
                total=total, returned=len(results), data={
                    "pattern": pattern, "target": target, "resolved_target": resolved,
                    "results": results, "supported_patterns": [*EDGE_PATTERNS, "file_summary"],
                    "confidence": "Static graph evidence only; verify in source. An empty result does not prove absence; dynamic dispatch may be unresolved.",
                })
