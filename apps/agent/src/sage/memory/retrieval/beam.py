"""Bounded diverse navigation over exact, lexical, and unknown evidence."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import PurePosixPath

from sage.memory.models import NodeType, RetrievalCandidate


def navigate(
    candidates: Sequence[RetrievalCandidate],
    *,
    beam_width: int,
    max_rounds: int,
    max_files: int,
) -> tuple[list[RetrievalCandidate], int]:
    """Select terminal files with stable ties and branch diversity."""

    if beam_width < 3:
        raise ValueError("SMRT beam width must be at least three.")
    ranked = sorted(candidates, key=lambda item: (-item.score, item.path))
    selected: list[RetrievalCandidate] = []
    seen_paths: set[str] = set()
    rounds = 0
    remaining = list(ranked)
    while remaining and len(selected) < max_files and rounds < max_rounds:
        rounds += 1
        round_items: list[RetrievalCandidate] = []
        used_ancestry: set[str] = set()
        for candidate in remaining:
            if candidate.path in seen_paths:
                continue
            if candidate.ancestry in used_ancestry and len(round_items) < beam_width - 1:
                continue
            round_items.append(candidate)
            used_ancestry.add(candidate.ancestry)
            if len(round_items) == beam_width:
                break
        if not round_items:
            round_items = remaining[:beam_width]
        for candidate in round_items:
            seen_paths.add(candidate.path)
            if candidate.node_type is NodeType.FILE:
                selected.append(candidate)
                if len(selected) == max_files:
                    break
        remaining = [item for item in remaining if item.path not in seen_paths]
    return selected, rounds


def lexical_candidates(
    results: Sequence[tuple[str, float]],
    *,
    node_types: dict[str, NodeType] | None = None,
) -> list[RetrievalCandidate]:
    return [
        RetrievalCandidate(
            path=path,
            node_type=(node_types or {}).get(path, NodeType.FILE),
            score=50.0 + score,
            evidence_tier="sparse_lexical",
            ancestry=(PurePosixPath(path).parts[0] if "/" in path else "."),
            reason="Sparse semantic memory matched the Issue",
        )
        for path, score in results
    ]
