"""High-precision evidence matching before lexical ranking."""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import PurePosixPath

from sage.memory.models import NodeType, RetrievalCandidate, SearchDocument

_PATH = re.compile(r"(?<![\w.-])(?:[\w.-]+/)+[\w.@+-]+")
_IDENTIFIER = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b")
_PATH_IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9]{2,}")
_GENERIC_PATH_IDENTIFIERS = {
    "app",
    "apps",
    "file",
    "files",
    "index",
    "javascript",
    "json",
    "jsx",
    "lib",
    "libs",
    "main",
    "package",
    "packages",
    "project",
    "projects",
    "python",
    "source",
    "sources",
    "src",
    "test",
    "tests",
    "toml",
    "tsx",
    "typescript",
    "yaml",
    "yml",
}


def exact_candidates(
    query: str,
    documents: Sequence[SearchDocument],
) -> list[RetrievalCandidate]:
    """Return deterministic candidates in descending evidence tiers."""

    normalized = query.casefold()
    explicit_paths = {item.rstrip(".,:;)") for item in _PATH.findall(query)}
    identifiers = {item.casefold() for item in _IDENTIFIER.findall(query)}
    ranked: dict[str, RetrievalCandidate] = {}

    for document in documents:
        path = document.path
        filename = PurePosixPath(path).name.casefold()
        fields = {
            "symbol": tuple(item.casefold() for item in document.symbols),
            "import": tuple(item.casefold() for item in document.imports),
        }
        if path in explicit_paths or path.casefold() in normalized:
            _keep(ranked, document, 100.0, "exact_path", "Issue names this path")
        elif filename and filename in normalized:
            _keep(ranked, document, 90.0, "exact_filename", "Issue names this file")
        elif identifiers.intersection(_path_identifiers(path)):
            _keep(
                ranked,
                document,
                85.0,
                "exact_path_identifier",
                "Issue names a specific path identifier",
            )
        elif identifiers.intersection(fields["symbol"]):
            _keep(ranked, document, 80.0, "exact_symbol", "Issue names an exported symbol")
        elif identifiers.intersection(fields["import"]):
            _keep(ranked, document, 70.0, "exact_import", "Issue names an import")

    return sorted(ranked.values(), key=lambda item: (-item.score, item.path))


def _keep(
    ranked: dict[str, RetrievalCandidate],
    document: SearchDocument,
    score: float,
    tier: str,
    reason: str,
) -> None:
    candidate = RetrievalCandidate(
        path=document.path,
        node_type=document.node_type,
        score=score,
        evidence_tier=tier,
        ancestry=_ancestry(document.path),
        reason=reason,
    )
    current = ranked.get(document.path)
    if current is None or candidate.score > current.score:
        ranked[document.path] = candidate


def _ancestry(path: str) -> str:
    parts = PurePosixPath(path).parts
    return parts[0] if len(parts) > 1 else "."


def _path_identifiers(path: str) -> set[str]:
    identifiers = {
        item.casefold()
        for part in PurePosixPath(path).parts
        for item in _PATH_IDENTIFIER.findall(part)
    }
    return identifiers - _GENERIC_PATH_IDENTIFIERS
