"""Weighted Leiden communities adapted from code-review-graph (MIT).

The lock only protects igraph's process-wide RNG adapter during a seeded
partition; it holds no repository or cross-run memory state.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from threading import Lock

import igraph as ig
import networkx as nx

_LEIDEN_LOCK = Lock()


def community_groups(directed: nx.DiGraph, *, _depth: int = 0) -> list[set[str]]:
    names = sorted(directed)
    positions = {name: index for index, name in enumerate(names)}
    weights: dict[tuple[int, int], float] = {}
    for source, target, data in directed.edges(data=True):
        if source == target:
            continue
        pair = tuple(sorted((positions[source], positions[target])))
        weights[pair] = max(weights.get(pair, 0), float(data.get("weight", 0.5)))
    if not weights:
        files: dict[str, set[str]] = defaultdict(set)
        for name in names:
            files[str(directed.nodes[name]["file_path"])].add(name)
        return list(files.values())
    edges = sorted(weights)
    graph = ig.Graph(n=len(names), edges=edges, directed=False)
    with _LEIDEN_LOCK:
        ig.set_random_number_generator(random.Random(42))
        try:
            partition = graph.community_leiden(
                objective_function="modularity",
                weights=[weights[edge] for edge in edges],
                resolution=max(0.05, 1 / math.log10(max(len(names), 10))),
                n_iterations=2,
            )
        finally:
            ig.set_random_number_generator(None)
    groups = [{names[index] for index in group} for group in partition]
    # A bounded refinement pass prevents a single giant cluster becoming a
    # repository-wide relevance shortcut. Never manufacture arbitrary chunks.
    if _depth < 2:
        threshold = max(10, len(names) // 4)
        refined = []
        for group in groups:
            if threshold < len(group) < len(names):
                refined.extend(community_groups(directed.subgraph(group).copy(), _depth=_depth + 1))
            else:
                refined.append(group)
        groups = refined
    membership = {name: index for index, group in enumerate(groups) for name in group}
    # A test follows the production community with the strongest TESTED_BY
    # evidence, not a large unrelated cluster formed by shared test helpers.
    targets: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for source, target, data in directed.edges(data=True):
        if data.get("kind") == "TESTED_BY" and directed.nodes[target].get("is_test"):
            targets[target][membership[source]] += 1
    for test, counts in sorted(targets.items()):
        chosen = min(counts, key=lambda index: (-counts[index], index))
        groups[membership[test]].discard(test)
        groups[chosen].add(test)
    return sorted((group for group in groups if group), key=lambda group: (-len(group), min(group)))
