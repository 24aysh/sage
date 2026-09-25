"""Deterministic Issue retrieval over one validated Repository retrieval index snapshot.

Identifier extraction and lexical-plus-graph ranking
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from time import perf_counter

from sage.domain.retrieval import (
    RetrievalRelationshipEvidence,
    RetrievalBudgets,
    RetrievalItem,
    RetrievalOutcome,
    RetrievalResult,
    RetrievalStatus,
    RetrievalCandidateDiagnostic,
)
from sage.harness.retrieval.parsing import detect_language
from sage.harness.retrieval.store import GraphStore

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
_NODE_KINDS = frozenset({"File", "Class", "Type", "Function", "Test", "Element", "Selector", "Endpoint", "Event", "ConfigKey"})
_EDGE_KINDS = ("CALLS", "REFERENCES", "IMPORTS_FROM", "TESTED_BY", "INHERITS", "IMPLEMENTS", "CONTAINS", "HANDLES", "TRIGGERS", "PUBLISHES", "CONSUMES")
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
    relationships: list[RetrievalRelationshipEvidence] = field(default_factory=list)
    channel_ranks: dict[str, int] = field(default_factory=dict)


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
    index_file: Path,
    budgets: RetrievalBudgets,
) -> RetrievalResult:
    """Rank lexical hits, expand the best seeds, and render bounded context."""

    started = perf_counter()
    signals = extract_issue_signals(issue_text, max_chars=budgets.max_issue_chars)
    candidates, search_modes = _lexical_candidates(store, signals, budgets)
    lexical_count = len(candidates)
    for rank, candidate in enumerate(sorted(candidates.values(), key=_candidate_sort_key), 1):
        candidate.channel_ranks["lexical"] = rank
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
            index_file=index_file,
            signals=signals,
            search_modes=search_modes,
            outcome=RetrievalOutcome.NO_LEXICAL_CANDIDATES,
            summary="The graph is ready, but the Issue produced no lexical matches.",
            started=started,
        )
    if not seeds:
        return _empty_result(
            store,
            index_file=index_file,
            signals=signals,
            search_modes=search_modes,
            outcome=RetrievalOutcome.BELOW_THRESHOLD,
            summary="Lexical candidates were found, but none passed the usefulness threshold.",
            lexical_candidates=lexical_count,
            started=started,
        )

    warnings: list[str] = []
    unresolved = 0
    for seed in seeds:
        try:
            unresolved += _expand_edges(store, seed, candidates, budgets.max_related_per_seed)
            _expand_flows(store, seed, candidates, budgets.max_related_per_seed)
            _expand_community(store, seed, candidates, min(2, budgets.max_related_per_seed))
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
    limited = _select_diverse(useful, budgets.max_results)
    items = tuple(_retrieval_item(candidate, rank=index) for index, candidate in enumerate(limited, 1))
    context, rendered_count, details_truncated = _render_context(
        items,
        indexed_sha=store.get_metadata("indexed_sha"),
        max_chars=budgets.max_chars,
    )
    visible = items[:rendered_count]
    total = len(useful)
    omitted = max(0, total - len(visible))
    truncated = omitted > 0 or details_truncated
    outcome = (
        RetrievalOutcome.USEFUL_CONTEXT_TRUNCATED
        if truncated
        else RetrievalOutcome.USEFUL_CONTEXT
    )
    duration_ms = round((perf_counter() - started) * 1_000, 2)
    return RetrievalResult(
        status=RetrievalStatus.USED,
        outcome=outcome,
        summary=(
            f"Retrieved {len(visible)} Issue-relevant graph item(s)"
            + (f"; {omitted} omitted by configured budgets." if omitted else ".")
            + (" Some signature/relationship details omitted by the context budget." if details_truncated else "")
        ),
        index_file=index_file,
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
        duration_ms=duration_ms,
        ranking_duration_ms=duration_ms,
        unresolved_edges=unresolved,
        diagnostics=tuple(RetrievalCandidateDiagnostic(
            qualified_name=str(candidate.node["qualified_name"]),
            channel_ranks=candidate.channel_ranks, reasons=tuple(sorted(candidate.reasons)),
            score=candidate.score,
            selection="displayed" if str(candidate.node["qualified_name"]) in {i.qualified_name for i in visible}
            else "display_budget" if candidate in limited else "rank_or_diversity_budget",
        ) for candidate in useful[:200]),
    )


def select_context_files(
    result: RetrievalResult, paths: set[str], *, max_chars: int,
    empty_outcome: RetrievalOutcome = RetrievalOutcome.RELEVANCE_REJECTED,
) -> RetrievalResult:
    """Re-render existing evidence only; never refill from discarded candidates."""
    accepted = tuple(item for item in result.items if item.file_path in paths)
    selected_count = len(accepted)
    names = {item.qualified_name for item in accepted}
    accepted = tuple(item.model_copy(update={"rank": index, "relationships": tuple(
        relation for relation in item.relationships if relation.seed_qualified_name in names
    )}) for index, item in enumerate(accepted, 1))
    if accepted:
        context, count, details_truncated = _render_context(
            accepted, indexed_sha=result.indexed_sha, max_chars=max_chars,
        )
        accepted = accepted[:count]
    else:
        context, count, details_truncated = "", 0, False
    truncated = count < selected_count or details_truncated
    if not accepted:
        context = ""
        if names:
            empty_outcome = RetrievalOutcome.CONTEXT_BUDGET_EXHAUSTED
    visible_names = {item.qualified_name for item in accepted}
    return result.model_copy(update={
        "items": accepted, "returned": len(accepted), "context": context,
        "context_chars": len(context), "omitted": max(0, result.total_candidates - len(accepted)),
        "truncated": truncated,
        "status": RetrievalStatus.USED if accepted else RetrievalStatus.NO_MATCH,
        "outcome": (RetrievalOutcome.USEFUL_CONTEXT_TRUNCATED if truncated
                    else RetrievalOutcome.USEFUL_CONTEXT) if accepted else empty_outcome,
        "summary": f"Context contains {len(accepted)} retrieval item(s) after file selection.",
        "diagnostics": tuple(item.model_copy(update={"selection": "displayed"
            if item.qualified_name in visible_names else "not_in_filtered_context"})
            for item in result.diagnostics),
    })


def _select_diverse(candidates: list[_Candidate], limit: int) -> list[_Candidate]:
    """Select an evidenced Issue map, then diversify remaining file locators."""
    if not candidates:
        return []
    priority = [candidates[0]]
    roles = {"behavior_owner"}
    candidates[0].reasons.add("role:behavior_owner")
    for candidate in candidates[1:]:
        if candidate.score < candidates[0].score * 0.45:
            continue
        path = PurePosixPath(str(candidate.node["file_path"]))
        evidence = candidate.reasons
        role = None
        if candidate.node["is_test"] and "test_for" in evidence:
            role = "test"
        elif candidate.node["kind"] == "Endpoint" or "caller_of" in evidence:
            role = "entry_or_caller"
        elif {"callee_of", "same_flow"} & evidence and {"repositories", "repository", "db", "persistence"} & set(path.parts):
            role = "persistence"
        if role and role not in roles:
            roles.add(role)
            candidate.reasons.add("role:" + role)
            priority.append(candidate)
    selected: list[_Candidate] = []
    deferred: list[_Candidate] = []
    counts: dict[str, int] = {}
    for candidate in priority + [c for c in candidates if c not in priority]:
        path = str(candidate.node["file_path"])
        wrapper = candidate.node["kind"] in {"File", "Class", "ConfigKey"}
        anchored = bool({"explicit_anchor", "exact_identifier", "path_match"} & candidate.reasons)
        if counts.get(path, 0) >= (1 if wrapper else 2) and not anchored:
            deferred.append(candidate)
        else:
            selected.append(candidate)
            counts[path] = counts.get(path, 0) + 1
    return (selected + deferred)[:limit]


def _lexical_candidates(
    store: GraphStore,
    signals: IssueSignals,
    budgets: RetrievalBudgets,
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
) -> int:
    qualified = str(seed.node["qualified_name"])
    marks = ",".join("?" for _ in _EDGE_KINDS)
    rows = store.rows(
        f"""WITH neighbors AS (
            SELECT e.kind, source_qualified, target_qualified, confidence,
                ROW_NUMBER() OVER (PARTITION BY n.qualified_name
                    ORDER BY confidence DESC, e.kind, source_qualified, target_qualified) AS position
            FROM edges e JOIN nodes n ON n.qualified_name =
                CASE WHEN source_qualified=? THEN target_qualified ELSE source_qualified END
            WHERE e.kind IN ({marks}) AND (source_qualified=? OR target_qualified=?)
                AND n.qualified_name != ?)
            SELECT * FROM neighbors WHERE position=1
            ORDER BY confidence DESC, kind, source_qualified, target_qualified LIMIT ?""",  # nosec B608
        (qualified, *_EDGE_KINDS, qualified, qualified, qualified, limit),
    )
    seen: set[tuple[str, str]] = set()
    unresolved = int(store.rows(
        f"SELECT COUNT(*) AS count FROM edges e WHERE kind IN ({marks}) "
        "AND (source_qualified=? OR target_qualified=?) AND NOT EXISTS "
        "(SELECT 1 FROM nodes n WHERE n.qualified_name=CASE WHEN source_qualified=? "
        "THEN target_qualified ELSE source_qualified END)",
        (*_EDGE_KINDS, qualified, qualified, qualified),
    )[0]["count"])
    for row in rows:
        outgoing = str(row["source_qualified"]) == qualified
        related = str(row["target_qualified"] if outgoing else row["source_qualified"])
        reason = _edge_reason(str(row["kind"]), outgoing=outgoing)
        if related == qualified or (related, reason) in seen:
            continue
        node = _safe_node(store.exact_node(related))
        if node is None:
            continue
        seen.add((related, reason))
        evidence = RetrievalRelationshipEvidence(
            reason=reason,
            relationship=str(row["kind"]),
            seed_qualified_name=qualified,
        )
        _merge_related(
            candidates,
            node,
            score=seed.score * (0.68 + 0.1 * float(row["confidence"] or 0.0)),
            reason=reason,
            evidence=evidence,
        )
        if len(seen) >= limit:
            break
    return unresolved


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
            evidence=RetrievalRelationshipEvidence(
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
           JOIN communities c ON c.id=seed_community.community_id AND c.size>=2 AND c.cohesion>=0.2
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
            evidence=RetrievalRelationshipEvidence(
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
    evidence: RetrievalRelationshipEvidence,
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


def _retrieval_item(candidate: _Candidate, *, rank: int) -> RetrievalItem:
    node = candidate.node
    return RetrievalItem(
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
    items: tuple[RetrievalItem, ...],
    *,
    indexed_sha: str | None,
    max_chars: int,
) -> tuple[str, int, bool]:
    lines = [
        "Graph-derived navigation context. Verify locations and behavior against source.",
        f"Accepted-base snapshot: {indexed_sha or 'unknown'}",
    ]
    rendered = "\n".join(lines)
    count = 0
    details_truncated = False
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
        if item.signature:
            signature = f"\n  signature: {_clip(item.signature, 180)}"
            if len(rendered) + len(block) + len(signature) <= max_chars:
                block += signature
            else:
                details_truncated = True
        for relationship in item.relationships[:3]:
            detail = (
                f"\n  {relationship.reason}: {relationship.relationship} link to "
                f"{_clip(relationship.seed_qualified_name, 120)}"
            )
            if len(rendered) + len(block) + len(detail) <= max_chars:
                block += detail
            else:
                details_truncated = True
        rendered += block
        count += 1
    return rendered[:max_chars], count, details_truncated


def _empty_result(
    store: GraphStore,
    *,
    index_file: Path,
    signals: IssueSignals,
    search_modes: tuple[str, ...],
    outcome: RetrievalOutcome,
    summary: str,
    started: float,
    lexical_candidates: int = 0,
) -> RetrievalResult:
    duration_ms = round((perf_counter() - started) * 1_000, 2)
    return RetrievalResult(
        status=RetrievalStatus.NO_MATCH,
        outcome=outcome,
        summary=summary,
        index_file=index_file,
        repository_id=store.get_metadata("repository_id"),
        indexed_sha=store.get_metadata("indexed_sha"),
        last_updated=store.get_metadata("last_updated"),
        search_modes=search_modes,
        query_terms=signals.terms,
        lexical_candidates=lexical_candidates,
        total_candidates=lexical_candidates,
        omitted=lexical_candidates,
        duration_ms=duration_ms,
        ranking_duration_ms=duration_ms,
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
        ("REFERENCES", True): "references",
        ("REFERENCES", False): "referenced_by",
        ("IMPLEMENTS", True): "implements",
        ("IMPLEMENTS", False): "implemented_by",
        ("HANDLES", True): "handles",
        ("HANDLES", False): "handled_by",
        ("TRIGGERS", True): "triggers",
        ("TRIGGERS", False): "triggered_by",
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
