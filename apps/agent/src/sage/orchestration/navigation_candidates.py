"""Deterministic, evidence-grounded navigation shortlists and source visibility."""

import hashlib
import json
import re
from pathlib import Path

from sage.domain.navigation import ActionCandidate, ReadAction, SearchAction, GraphAction, SearchMatch, NavigationDecision
from sage.errors import RepositoryError
from sage.repository.snippets import source_identity

POLICY_VERSION = "navigation-v1-experimental"


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def fingerprint(action: ReadAction | SearchAction | GraphAction) -> str:
    return digest(action.model_dump_json())


def excerpt_selection(decision: NavigationDecision) -> tuple[str, ...]:
    """Provisional policy, separately evaluable from the raw provider judgments."""
    return tuple(sorted((key for key, score in decision.scores.items()
        if score >= 2 and decision.confidences.get(key, 0) >= .5),
        key=lambda key: (-decision.scores[key], key))[:2])


def accept_action(decision: NavigationDecision, candidate: ActionCandidate) -> bool:
    threshold = {"read_file": .65, "search_text": .75, "query_graph_tool": .8}[candidate.action.kind]
    return decision.confidence >= .5 and decision.probabilities.get(candidate.id, 0) >= threshold


def numbered_lines(source: str) -> set[int]:
    lines = source.splitlines()
    return {int(match.group(1)) for index, line in enumerate(lines)
            if (match := re.match(r"^(\d+) \| ", line)) and not (
                index + 1 < len(lines) and "truncated approximately" in lines[index + 1])}


def shortlist(*, root: Path, matches: tuple[SearchMatch, ...], source: str,
              path: str, scope: str, query: str, actions: bool,
              nodes: list[dict], visible: dict[tuple[str, str], set[int]],
              attempted: set[str], anchors: tuple[str, ...] = ()) -> tuple[tuple[ActionCandidate, ...], dict[str, str]]:
    """No searches, graph queries, or model calls while constructing options."""
    groups: list[list[tuple[ReadAction | SearchAction | GraphAction, str]]] = [[], [], []]
    identities: dict[str, str] = {}

    def readable(candidate_path: str) -> str | None:
        if candidate_path in identities:
            return identities[candidate_path]
        if len(identities) >= 16:
            return None
        try:
            identity = source_identity(root, candidate_path)
            identities[candidate_path] = identity
            return identity
        except (RepositoryError, OSError, ValueError):
            return None

    def read(candidate_path: str, line: int, evidence: str) -> None:
        if len(candidate_path) > 500 or (identity := readable(candidate_path)) is None:
            return
        start = max(1, line - 5)
        end = start + 39
        if line in visible.get((candidate_path, identity), set()):
            return
        for action, _ in groups[0]:
            if action.path == candidate_path and action.start_line <= end and start <= action.end_line:
                return
        groups[0].append((ReadAction(path=candidate_path, start_line=start, end_line=end), evidence[:500]))

    # Round-robin paths before spending remaining slots on one file.
    ordered = sorted(enumerate(matches[:100]), key=lambda pair: (
        sum(m.path == pair[1].path for m in matches[:pair[0]]), pair[0]))
    for _, match in ordered:
        read(match.path, match.line, f"Search match {match.path}:{match.line}: {match.text[:350]}")
        if len(groups[0]) >= 8:
            break
    if actions:
        if path != ".":
            readable(path)
        for anchor in anchors[:8]:
            read(anchor, 1, "Explicit Issue or current-plan path: " + anchor)
        for node in nodes[:20]:
            if node.get("file_path") and isinstance(node.get("line_start"), int):
                read(node["file_path"], node["line_start"], f"Accepted-base locator: {node.get('qualified_name')}")
        # Identifiers are copied exactly from declarations in retained observations.
        identifiers = re.findall(r"\b(?:def|class|function|const|let|var)\s+([A-Za-z_]\w{2,199})", source)
        for identifier in dict.fromkeys(identifiers):
            if identifier != query and len(groups[1]) < 3:
                groups[1].append((SearchAction(query=identifier, path=scope),
                                  f"Declaration observed in {path}: {identifier}"))
        for node in nodes[:20]:
            target = node.get("qualified_name", "")
            if not target or len(target) > 1000 or not readable(node.get("file_path", "")):
                continue
            if node["file_path"] != path and target not in source:
                continue
            for pattern in ("callers_of", "tests_for", "callees_of"):
                groups[2].append((GraphAction(pattern=pattern, target=target), f"Known graph target: {target}"))
    selected = []
    used = set(attempted)
    while any(groups) and len(selected) < (6 if actions else 8):
        for group in groups:
            if not group or len(selected) >= (6 if actions else 8):
                continue
            action, evidence = group.pop(0)
            identity = fingerprint(action)
            if identity in used:
                continue
            used.add(identity)
            selected.append(ActionCandidate(id=f"c{len(selected)}", action=action, evidence=evidence))
    return tuple(selected), identities


def render_observation(action: ReadAction | SearchAction | GraphAction, source: str, limit: int) -> str:
    header = "\n\n<navigation-observation " + json.dumps(action.model_dump(), ensure_ascii=False) + ">\n"
    suffix = "\n</navigation-observation>"
    room = limit - len(header) - len(suffix)
    if room < 100:
        return ""
    if len(source) > room:
        marker = "\n[observation truncated]"
        prefix = source[:max(0, room - len(marker))]
        if "\n" not in prefix:
            return ""
        source = prefix.rsplit("\n", 1)[0] + marker
    return header + source + suffix if len(header + source + suffix) <= limit else ""
