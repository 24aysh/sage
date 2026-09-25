"""Change-scoped risk and test context"""

from __future__ import annotations

from collections import deque

from sage.harness.retrieval.store import GraphStore, _public_node

_SECURITY_KEYWORDS = frozenset({
    "auth", "login", "password", "token", "session", "crypt", "secret",
    "credential", "permission", "sql", "query", "execute", "connect",
    "socket", "request", "http", "sanitize", "validate", "encrypt",
    "decrypt", "hash", "sign", "verify", "admin", "privilege",
})


def transitive_tests(store: GraphStore, qualified_name: str) -> list[str]:
    """Follow callers and explicit production-to-test links, cycle safely."""
    seen = {qualified_name}
    pending = deque([(qualified_name, 0)])
    tests: set[str] = set()
    while pending and len(seen) <= 2000:
        name, depth = pending.popleft()
        tests.update(r["target_qualified"] for r in store.rows(
            "SELECT target_qualified FROM edges WHERE kind='TESTED_BY' AND source_qualified=?", (name,)))
        if depth >= 15:
            continue
        for row in store.rows(
            "SELECT DISTINCT n.qualified_name, n.is_test FROM edges e JOIN nodes n ON n.qualified_name=e.source_qualified "
            "WHERE e.kind IN ('CALLS','TRIGGERS') AND e.target_qualified=? ORDER BY n.qualified_name", (name,),
        ):
            if row["is_test"]:
                tests.add(row["qualified_name"])
            elif row["qualified_name"] not in seen:
                seen.add(row["qualified_name"])
                pending.append((row["qualified_name"], depth + 1))
    return sorted(tests)


def change_context(store: GraphStore, changed_files: list[str], limit: int) -> dict:
    selected = set(changed_files)
    rows = [r for r in store.rows("SELECT * FROM nodes WHERE kind IN ('Function','Test') ORDER BY qualified_name")
            if r["file_path"] in selected]
    communities = {r["qualified_name"]: r["community_id"] for r in store.rows("SELECT * FROM node_communities")}
    functions = []
    gaps = []
    flow_ids: set[int] = set()
    for row in rows:
        qn = row["qualified_name"]
        callers = store.rows("SELECT source_qualified FROM edges WHERE kind='CALLS' AND target_qualified=?", (qn,))
        flows = store.rows("SELECT f.id, f.criticality FROM flows f JOIN flow_memberships m ON m.flow_id=f.id WHERE m.qualified_name=?", (qn,))
        tests = transitive_tests(store, qn)
        crossing = {r["source_qualified"] for r in callers
                    if communities.get(r["source_qualified"]) is not None
                    and communities.get(qn) is not None
                    and communities[r["source_qualified"]] != communities[qn]}
        security = any(word in qn.casefold() for word in _SECURITY_KEYWORDS)
        risk = min(sum(float(f["criticality"]) for f in flows), .25)
        risk += min(len(crossing) * .05, .15)
        risk += .30 - min(len(tests) / 5, 1) * .25
        risk += .20 if security else 0
        risk += min(len(callers) / 20, .10)
        functions.append({**_public_node(row), "risk_score": round(min(risk, 1), 4), "tests": tests[:10]})
        if not tests and not row["is_test"]:
            gaps.append(_public_node(row))
        flow_ids.update(f["id"] for f in flows)
    functions.sort(key=lambda r: (-r["risk_score"], r["qualified_name"]))
    flows = [r for r in store.rows("SELECT id,name,criticality,node_count,file_count FROM flows ORDER BY criticality DESC,id") if r["id"] in flow_ids]
    return {
        "changed_files": sorted(selected)[:100], "changed_files_total": len(selected),
        "changed_functions": functions[:limit], "changed_functions_total": len(functions),
        "risk_score": max((f["risk_score"] for f in functions), default=0),
        "affected_flows": flows[:limit], "affected_flows_total": len(flows),
        "test_gaps": gaps[:limit], "test_gaps_total": len(gaps),
        "review_priorities": [f"Verify {f['qualified_name']} (risk {f['risk_score']:.2f})" for f in functions[:3]],
        "limitations": "Accepted-base static impact, not a Reviewer verdict. Whole-file mapping is conservative; verify current source and dynamic dispatch.",
    }
