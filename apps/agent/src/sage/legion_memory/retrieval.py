"""Deterministic Issue retrieval over one validated Legion Memory snapshot.

Identifier extraction and lexical-plus-graph ranking are adapted from the
MIT-licensed code-review-graph project (copyright 2026 Tirth Kanani).
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from time import perf_counter

from sage.domain.memory import (
    MemoryRelationshipEvidence,
    MemoryRetrievalBudgets,
    MemoryRetrievalItem,
    MemoryRetrievalOutcome,
    MemoryRetrievalResult,
    MemoryRetrievalStatus,
)
from sage.legion_memory.parsing import detect_language
from sage.legion_memory.store import GraphStore

_PATH_RE = re.compile(
    r"(?<![\w/.-])(?:[A-Za-z0-9_.@+-]+/)*"
    r"[A-Za-z0-9_@+-]+\.[A-Za-z0-9]{1,12}(?::\d+)?"
)
_QUALIFIED_RE = re.compile(
    r"\b[A-Za-z_][\w./-]*::[A-Za-z_$][\w$]*(?:::[A-Za-z_$][\w$]*)*\b"
)
_DOTTED_RE = re.compile(r"\b[A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)+\b")
_SNAKE_RE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")
_PASCAL_RE = re.compile(r"\b[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+\b")
_ERROR_RE = re.compile(r"\b[A-Za-z_][\w]*(?:Error|Exception|Failure)\b")
_BACKTICK_RE = re.compile(r"`([^`\n]{1,200})`")
_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")

_STOP_WORDS = frozenset(
    {
        "about", "after", "again", "also", "and", "are", "because", "been",
        "before", "being", "but", "can", "change", "code", "could", "does",
        "error", "file", "fix", "for", "from", "has", "have", "into", "issue",
        "its", "make", "not", "only", "our", "please", "repository", "should",
        "that", "the", "their", "then", "there", "these", "they", "this", "use",
        "using", "was", "when", "where", "which", "will", "with", "would",
    }
)
_NODE_KINDS = frozenset({"File", "Class", "Type", "Function", "Test"})
_EDGE_KINDS = ("CALLS", "IMPORTS_FROM", "TESTED_BY", "INHERITS", "CONTAINS")
_MAX_QUERY_TERMS = 24


@dataclass(frozen=True)
class IssueSignals:
    """Bounded strings extracted from untrusted Issue text."""

    normalized: str
    paths: tuple[str, ...]
    identifiers: tuple[str, ...]
    terms: tuple[str, ...]


@dataclass
class _Candidate:
    node: dict[str, object]
    score: float
    lexical: bool
    reasons: set[str] = field(default_factory=set)
    relationships: list[MemoryRelationshipEvidence] = field(default_factory=list)


def extract_issue_signals(issue_text: str, *, max_chars: int) -> IssueSignals:
    """Normalize an Issue as data and extract bounded retrieval signals."""

    if not issue_text.strip():
        raise ValueError("Issue text cannot be empty.")
    if len(issue_text) > max_chars:
        raise ValueError(f"Issue text exceeds the {max_chars}-character limit.")
    normalized = " ".join(issue_text.replace("\x00", " ").split())
    paths = _ordered_unique(
        path
        for match in _PATH_RE.findall(normalized)
        if (path := _safe_issue_path(match)) is not None
    )
    explicit = [*_QUALIFIED_RE.findall(normalized)]
    for pattern in (_DOTTED_RE, _SNAKE_RE, _PASCAL_RE, _ERROR_RE):
        explicit.extend(pattern.findall(normalized))
    explicit.extend(_BACKTICK_RE.findall(normalized))
    identifiers = _ordered_unique(
        value.casefold()
        for raw in explicit
        if (value := raw.strip(" `.,:;()[]{}")) and 3 <= len(value) <= 200
    )
    terms = _ordered_unique(
        token.casefold()
        for token in _WORD_RE.findall(normalized)
        if token.casefold() not in _STOP_WORDS and len(token) <= 200
    )
    search_terms = _ordered_unique((*identifiers, *terms))[:_MAX_QUERY_TERMS]
    return IssueSignals(
        normalized=normalized,
        paths=paths[:_MAX_QUERY_TERMS],
        identifiers=identifiers[:_MAX_QUERY_TERMS],
        terms=search_terms,
    )


def retrieve_issue_context(
    issue_text: str,
    store: GraphStore,
    *,
    memory_file: Path,
    budgets: MemoryRetrievalBudgets,
) -> MemoryRetrievalResult:
    """Rank lexical hits, expand the best seeds, and render bounded context."""

    started = perf_counter()
    signals = extract_issue_signals(issue_text, max_chars=budgets.max_issue_chars)
    candidates, search_modes = _lexical_candidates(store, signals, budgets)
    lexical_count = len(candidates)
    seeds = sorted(
        (
            candidate
            for candidate in candidates.values()
            if candidate.score >= budgets.usefulness_threshold
        ),
        key=_candidate_sort_key,
    )[: budgets.max_seeds]
    if not candidates:
        return _empty_result(
            store,
            memory_file=memory_file,
            signals=signals,
            search_modes=search_modes,
            outcome=MemoryRetrievalOutcome.NO_LEXICAL_CANDIDATES,
            summary="The graph is ready, but the Issue produced no lexical matches.",
            started=started,
        )
    if not seeds:
        return _empty_result(
            store,
            memory_file=memory_file,
            signals=signals,
            search_modes=search_modes,
            outcome=MemoryRetrievalOutcome.BELOW_THRESHOLD,
            summary="Lexical candidates were found, but none passed the usefulness threshold.",
            lexical_candidates=lexical_count,
            started=started,
        )

    warnings: list[str] = []
    for seed in seeds:
        try:
            _expand_edges(store, seed, candidates, budgets.max_related_per_seed)
            _expand_flows(store, seed, candidates, budgets.max_related_per_seed)
            _expand_community(store, seed, candidates, budgets.max_related_per_seed)
        except sqlite3.Error as error:
            warnings.append(
                f"Skipped expansion for {_clip(str(seed.node['qualified_name']), 120)}: "
                f"{type(error).__name__}."
            )

    useful = sorted(
        (
            candidate
            for candidate in candidates.values()
            if candidate.score >= budgets.usefulness_threshold
        ),
        key=_candidate_sort_key,
    )
    limited = useful[: budgets.max_results]
    items = tuple(_retrieval_item(candidate, rank=index) for index, candidate in enumerate(limited, 1))
    context, rendered_count = _render_context(
        items,
        indexed_sha=store.get_metadata("indexed_sha"),
        max_chars=budgets.max_chars,
    )
    visible = items[:rendered_count]
    total = len(useful)
    omitted = max(0, total - len(visible))
    truncated = omitted > 0
    outcome = (
        MemoryRetrievalOutcome.USEFUL_CONTEXT_TRUNCATED
        if truncated
        else MemoryRetrievalOutcome.USEFUL_CONTEXT
    )
    return MemoryRetrievalResult(
        status=MemoryRetrievalStatus.USED,
        outcome=outcome,
        summary=(
            f"Retrieved {len(visible)} Issue-relevant graph item(s)"
            + (f"; {omitted} omitted by configured budgets." if omitted else ".")
        ),
        memory_file=memory_file,
        repository_id=store.get_metadata("repository_id"),
        indexed_sha=store.get_metadata("indexed_sha"),
        last_updated=store.get_metadata("last_updated"),
        search_modes=search_modes,
        query_terms=signals.terms,
        lexical_candidates=lexical_count,
        expanded_candidates=sum(not item.lexical for item in candidates.values()),
        total_candidates=total,
        returned=len(visible),
        omitted=omitted,
        truncated=truncated,
        context=context,
        context_chars=len(context),
        items=visible,
        warnings=tuple(warnings[:20]),
        duration_ms=round((perf_counter() - started) * 1_000, 2),
    )


def _lexical_candidates(
    store: GraphStore,
    signals: IssueSignals,
    budgets: MemoryRetrievalBudgets,
) -> tuple[dict[str, _Candidate], tuple[str, ...]]:
    raw_nodes: dict[str, dict[str, object]] = {}
    exact_names: set[str] = set()
    if signals.paths:
        marks = ",".join("?" for _ in signals.paths)
        for row in store.rows(
            f"SELECT * FROM nodes WHERE file_path IN ({marks})",  # nosec B608
            tuple(signals.paths),
        ):
            node = _safe_node(row)
            if node:
                raw_nodes[str(node["qualified_name"])] = node
                exact_names.add(str(node["qualified_name"]))
    if signals.terms:
        terms = tuple(signals.terms)
        marks = ",".join("?" for _ in terms)
        for row in store.rows(
            f"""SELECT * FROM nodes
                WHERE lower(name) IN ({marks})
                   OR lower(qualified_name) IN ({marks})""",  # nosec B608
            (*terms, *terms),
        ):
            node = _safe_node(row)
            if node:
                raw_nodes[str(node["qualified_name"])] = node
                exact_names.add(str(node["qualified_name"]))

    search_mode = "none"
    searched_names: set[str] = set()
    if signals.terms:
        candidate_limit = min(100, max(20, budgets.max_results * 4))
        rows, search_mode = store.search(
            " ".join(signals.terms[:12]),
            kind=None,
            limit=candidate_limit,
        )
        for row in rows:
            node = _safe_node(row)
            if node:
                qualified = str(node["qualified_name"])
                raw_nodes[qualified] = node
                searched_names.add(qualified)

    candidates: dict[str, _Candidate] = {}
    for qualified_name, node in raw_nodes.items():
        score, reasons = _score_lexical(
            node,
            signals,
            search_mode=search_mode if qualified_name in searched_names else None,
        )
        candidates[qualified_name] = _Candidate(
            node=node,
            score=score,
            lexical=True,
            reasons=reasons,
        )
    modes: list[str] = []
    if exact_names:
        modes.append("exact")
    if search_mode != "none":
        modes.append(search_mode)
    return candidates, tuple(modes or ["none"])


def _score_lexical(
    node: dict[str, object],
    signals: IssueSignals,
    *,
    search_mode: str | None,
) -> tuple[float, set[str]]:
    score = 0.0
    reasons: set[str] = set()
    name = str(node["name"]).casefold()
    qualified = str(node["qualified_name"]).casefold()
    file_path = str(node["file_path"]).casefold()
    signature = str(node["signature"]).casefold()
    for path in signals.paths:
        folded = path.casefold()
        if file_path == folded:
            score += 18.0
            reasons.add("path_match")
        elif file_path.endswith(f"/{folded}"):
            score += 12.0
            reasons.add("path_match")
    for term in signals.terms:
        if qualified == term:
            score += 20.0
            reasons.add("exact_identifier")
        elif name == term:
            score += 12.0
            reasons.add("exact_identifier")
        elif term in qualified and term in signals.identifiers:
            score += 6.0
            reasons.add("identifier_match")
    term_hits = sum(
        term in " ".join((name, qualified, file_path, signature))
        for term in signals.terms
    )
    if term_hits:
        score += min(6.0, term_hits * 1.25)
    if search_mode:
        score += 3.0
        reasons.add(search_mode)
    if bool(node["is_test"]) and any(term.startswith("test") for term in signals.terms):
        score += 2.0
        reasons.add("test_match")
    return round(score, 6), reasons or {"keyword"}


def _expand_edges(
    store: GraphStore,
    seed: _Candidate,
    candidates: dict[str, _Candidate],
    limit: int,
) -> None:
    qualified = str(seed.node["qualified_name"])
    marks = ",".join("?" for _ in _EDGE_KINDS)
    rows = store.rows(
        f"""SELECT DISTINCT kind, source_qualified, target_qualified, confidence
            FROM edges
            WHERE kind IN ({marks})
              AND (source_qualified=? OR target_qualified=?)
            ORDER BY confidence DESC, kind, source_qualified, target_qualified
            LIMIT ?""",  # nosec B608
        (*_EDGE_KINDS, qualified, qualified, limit * 3),
    )
    seen: set[tuple[str, str]] = set()
    for row in rows:
        outgoing = str(row["source_qualified"]) == qualified
        related = str(row["target_qualified"] if outgoing else row["source_qualified"])
        reason = _edge_reason(str(row["kind"]), outgoing=outgoing)
        if related == qualified or (related, reason) in seen:
            continue
        seen.add((related, reason))
        node = _safe_node(store.node(related))
        if node is None:
            continue
        evidence = MemoryRelationshipEvidence(
            reason=reason,
            relationship=str(row["kind"]),
            seed_qualified_name=qualified,
        )
        _merge_related(
            candidates,
            node,
            score=seed.score * 0.68 + float(row["confidence"] or 0.0),
            reason=reason,
            evidence=evidence,
        )
        if len(seen) >= limit:
            break


def _expand_flows(
    store: GraphStore,
    seed: _Candidate,
    candidates: dict[str, _Candidate],
    limit: int,
) -> None:
    qualified = str(seed.node["qualified_name"])
    rows = store.rows(
        """SELECT n.*, f.id AS relation_id
           FROM flow_memberships seed_membership
           JOIN flows f ON f.id=seed_membership.flow_id
           JOIN flow_memberships related ON related.flow_id=f.id
           JOIN nodes n ON n.qualified_name=related.qualified_name
           WHERE seed_membership.qualified_name=? AND related.qualified_name != ?
           ORDER BY f.criticality DESC, related.position, n.qualified_name
           LIMIT ?""",
        (qualified, qualified, limit),
    )
    for row in rows:
        node = _safe_node(row)
        if node is None:
            continue
        _merge_related(
            candidates,
            node,
            score=seed.score * 0.52,
            reason="same_flow",
            evidence=MemoryRelationshipEvidence(
                reason="same_flow",
                relationship=f"FLOW:{row['relation_id']}",
                seed_qualified_name=qualified,
            ),
        )


def _expand_community(
    store: GraphStore,
    seed: _Candidate,
    candidates: dict[str, _Candidate],
    limit: int,
) -> None:
    qualified = str(seed.node["qualified_name"])
    rows = store.rows(
        """SELECT n.*, seed_community.community_id AS relation_id
           FROM node_communities seed_community
           JOIN node_communities related
             ON related.community_id=seed_community.community_id
           JOIN nodes n ON n.qualified_name=related.qualified_name
           WHERE seed_community.qualified_name=? AND related.qualified_name != ?
           ORDER BY n.is_test, n.file_path, n.line_start, n.qualified_name
           LIMIT ?""",
        (qualified, qualified, limit),
    )
    for row in rows:
        node = _safe_node(row)
        if node is None:
            continue
        _merge_related(
            candidates,
            node,
            score=seed.score * 0.42,
            reason="same_community",
            evidence=MemoryRelationshipEvidence(
                reason="same_community",
                relationship=f"COMMUNITY:{row['relation_id']}",
                seed_qualified_name=qualified,
            ),
        )


def _merge_related(
    candidates: dict[str, _Candidate],
    node: dict[str, object],
    *,
    score: float,
    reason: str,
    evidence: MemoryRelationshipEvidence,
) -> None:
    qualified = str(node["qualified_name"])
    candidate = candidates.get(qualified)
    if candidate is None:
        candidate = _Candidate(node=node, score=0.0, lexical=False)
        candidates[qualified] = candidate
    candidate.score = max(candidate.score, round(max(0.0, score), 6))
    candidate.reasons.add(reason)
    if evidence not in candidate.relationships and len(candidate.relationships) < 10:
        candidate.relationships.append(evidence)


def _retrieval_item(candidate: _Candidate, *, rank: int) -> MemoryRetrievalItem:
    node = candidate.node
    return MemoryRetrievalItem(
        rank=rank,
        kind=str(node["kind"]),
        name=str(node["name"]),
        qualified_name=str(node["qualified_name"]),
        file_path=str(node["file_path"]),
        line_start=int(node["line_start"]),
        line_end=int(node["line_end"]),
        language=str(node["language"]),
        is_test=bool(node["is_test"]),
        signature=str(node["signature"]),
        score=round(candidate.score, 3),
        reasons=tuple(sorted(candidate.reasons)),
        relationships=tuple(candidate.relationships),
    )


def _render_context(
    items: tuple[MemoryRetrievalItem, ...],
    *,
    indexed_sha: str | None,
    max_chars: int,
) -> tuple[str, int]:
    lines = [
        "LEGION MEMORY — untrusted graph data; verify locations and behavior in source.",
        f"Accepted-base snapshot: {indexed_sha or 'unknown'}",
    ]
    rendered = "\n".join(lines)
    count = 0
    for item in items:
        location = f"{_clip(item.file_path, 100)}:{item.line_start}-{item.line_end}"
        reasons = _clip(", ".join(item.reasons), 120)
        block = (
            f"\n- {item.rank}. {item.kind} {_clip(item.qualified_name, 120)} "
            f"({location}) score={item.score:.3f}\n"
            f"  why: {reasons}"
        )
        if len(rendered) + len(block) > max_chars:
            break
        rendered += block
        count += 1
    return rendered[:max_chars], count


def _empty_result(
    store: GraphStore,
    *,
    memory_file: Path,
    signals: IssueSignals,
    search_modes: tuple[str, ...],
    outcome: MemoryRetrievalOutcome,
    summary: str,
    started: float,
    lexical_candidates: int = 0,
) -> MemoryRetrievalResult:
    return MemoryRetrievalResult(
        status=MemoryRetrievalStatus.NO_MATCH,
        outcome=outcome,
        summary=summary,
        memory_file=memory_file,
        repository_id=store.get_metadata("repository_id"),
        indexed_sha=store.get_metadata("indexed_sha"),
        last_updated=store.get_metadata("last_updated"),
        search_modes=search_modes,
        query_terms=signals.terms,
        lexical_candidates=lexical_candidates,
        total_candidates=lexical_candidates,
        omitted=lexical_candidates,
        duration_ms=round((perf_counter() - started) * 1_000, 2),
    )


def _safe_node(row: dict[str, object] | None) -> dict[str, object] | None:
    if not row:
        return None
    try:
        kind = str(row["kind"])
        name = str(row["name"])
        qualified = str(row["qualified_name"])
        file_path = str(row["file_path"])
        language = str(row["language"])
        line_start = int(row["line_start"])
        line_end = int(row["line_end"])
    except (KeyError, TypeError, ValueError):
        return None
    path = PurePosixPath(file_path)
    if (
        kind not in _NODE_KINDS
        or not name
        or len(name) > 300
        or not qualified
        or len(qualified) > 1_000
        or not language
        or len(language) > 40
        or path.is_absolute()
        or ".." in path.parts
        or len(file_path) > 500
        or line_start < 1
        or line_end < line_start
    ):
        return None
    return {
        "kind": kind,
        "name": name,
        "qualified_name": qualified,
        "file_path": file_path,
        "line_start": line_start,
        "line_end": line_end,
        "language": language,
        "is_test": bool(row.get("is_test", False)),
        "signature": str(row.get("signature") or "")[:500],
    }


def _edge_reason(kind: str, *, outgoing: bool) -> str:
    return {
        ("CALLS", True): "callee_of",
        ("CALLS", False): "caller_of",
        ("IMPORTS_FROM", True): "import_of",
        ("IMPORTS_FROM", False): "importer_of",
        ("TESTED_BY", True): "test_for",
        ("TESTED_BY", False): "tested_symbol",
        ("INHERITS", True): "base_of",
        ("INHERITS", False): "inheritor_of",
        ("CONTAINS", True): "child_of",
        ("CONTAINS", False): "parent_of",
    }.get((kind, outgoing), "graph_neighbor")


def _safe_issue_path(value: str) -> str | None:
    candidate = value.rsplit(":", 1)[0].replace("\\", "/").strip("`'\"")
    path = PurePosixPath(candidate)
    if (
        path.is_absolute()
        or ".." in path.parts
        or len(candidate) > 500
        or detect_language(candidate) is None
    ):
        return None
    return path.as_posix()


def _ordered_unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value) for value in values if str(value)))


def _candidate_sort_key(candidate: _Candidate) -> tuple[float, int, str]:
    return (
        -candidate.score,
        int(bool(candidate.node["is_test"])),
        str(candidate.node["qualified_name"]),
    )


def _clip(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 1] + "…"
