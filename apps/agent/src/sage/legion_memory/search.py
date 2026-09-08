"""Rank fusion adapted from MIT code-review-graph search.py (Tirth Kanani)."""

from __future__ import annotations

import re
import sqlite3

from sage.domain.embeddings import VectorStatus
from sage.legion_memory.store import GraphStore, _public_node
from sage.legion_memory.vectors import VectorIndex


def rrf_merge(*rankings: list[tuple[str, float]], k: int = 60) -> list[tuple[str, float]]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        seen: set[str] = set()
        for rank, (name, _) in enumerate(ranking, 1):
            if name not in seen:
                scores[name] = scores.get(name, 0) + 1 / (k + rank)
                seen.add(name)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


def hybrid_search(
    store: GraphStore,
    query: str,
    *,
    vectors: VectorIndex,
    limit: int = 20,
    kind: str | None = None,
    context_files: tuple[str, ...] = (),
) -> tuple[list[dict[str, object]], str, VectorStatus]:
    fetch = limit * 3
    terms = list(dict.fromkeys(re.findall(r"[\w]+", query)))[:24]
    phrase = ' OR '.join('"' + term.replace('"', '""') + '"' for term in terms)
    try:
        matches = store.rows(
            "SELECT n.qualified_name, bm25(nodes_fts) AS rank FROM nodes_fts "
            "JOIN nodes n ON n.id=nodes_fts.node_id WHERE nodes_fts MATCH ? "
            "ORDER BY rank, n.qualified_name LIMIT ?", (phrase, fetch),
        )
    except sqlite3.OperationalError:
        matches = []
    lexical = [(str(n["qualified_name"]), -float(n["rank"])) for n in matches]
    semantic, status = vectors.search(store, query, limit=fetch)
    mode = "hybrid" if lexical and semantic else "semantic" if semantic else "fts" if lexical else "none"
    if not lexical and not semantic:
        nodes, fallback = store.search(query, kind=kind, limit=limit)
        return nodes, fallback, status
    identifiers = re.findall(r"\b[A-Za-z_]\w*(?:\.\w+)+\b|\b\w+_\w+\b|\b[A-Z][a-z]+(?:[A-Z]\w*)+\b", query)
    channel_ranks = {label: {name: rank for rank, (name, _) in enumerate(ranking, 1)}
                     for label, ranking in (("fts", lexical), ("semantic", semantic))}
    nodes = []
    for name, score in rrf_merge(lexical, semantic):
        raw = store.exact_node(name)
        if not raw or (kind and raw["kind"] != kind):
            continue
        boost = 1.0
        if re.match(r"^[A-Z][a-z]", query) and raw["kind"] in {"Class", "Type"}:
            boost *= 1.5
        if "_" in query and raw["kind"] == "Function":
            boost *= 1.5
        if "." in query and query.casefold() in name.casefold():
            boost *= 2
        if any(identifier.casefold() in name.casefold() for identifier in identifiers):
            boost *= 2
        if raw["file_path"] in context_files:
            boost *= 1.5
        node = _public_node(raw)
        ranks = {label: ranking[name] for label, ranking in channel_ranks.items() if name in ranking}
        node.update(score=score * boost, search_modes=list(ranks), channel_ranks=ranks)
        nodes.append(node)
    nodes.sort(key=lambda n: (-float(n["score"]), str(n["qualified_name"])))
    return nodes[:limit], mode, status
