"""Deterministic repository discovery before V2 intake."""

from __future__ import annotations

import logging
import re
from collections import Counter
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict, Field

from sage.errors import RepositoryError
from sage.repository.files import read_file
from sage.repository.inventory import LiteralMatch, search_literal_matches, tracked_inventory
from sage.sandbox.base import Sandbox

logger = logging.getLogger(__name__)

_PATH_TOKEN = re.compile(r"(?<![\w.-])(?:[\w.-]+/)+[\w.-]+")
_IDENTIFIER = re.compile(r"\b[^\W\d]\w{2,79}\b", re.UNICODE)
_STOP_WORDS = frozenset(
    {
        "add",
        "after",
        "also",
        "and",
        "before",
        "change",
        "create",
        "does",
        "feature",
        "file",
        "fix",
        "from",
        "have",
        "into",
        "issue",
        "make",
        "must",
        "should",
        "that",
        "the",
        "this",
        "when",
        "with",
    }
)
_MANIFEST_NAMES = frozenset(
    {
        "pyproject.toml",
        "package.json",
        "Cargo.toml",
        "go.mod",
        "Makefile",
        "tox.ini",
        "noxfile.py",
    }
)
_DOC_NAMES = frozenset({"README.md", "README.rst", "ARCHITECTURE.md", "CONTRIBUTING.md"})
_ENTRY_NAMES = frozenset({"main.py", "app.py", "cli.py", "__main__.py", "index.ts", "index.js"})
_LANGUAGE_EXTENSIONS = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".rb": "ruby",
}


class RepositoryExcerpt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str = Field(min_length=1, max_length=1_000)
    content: str = Field(max_length=12_000)


class RepositoryMap(BaseModel):
    """Compact bounded repository evidence for V2 context compilation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    base_sha: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    tracked_file_count: int = Field(ge=0)
    tracked_paths_sample: tuple[str, ...] = Field(max_length=5_000)
    top_level_summary: tuple[str, ...] = Field(max_length=200)
    language_summary: dict[str, int]
    manifests: tuple[str, ...] = Field(max_length=30)
    test_roots: tuple[str, ...] = Field(max_length=30)
    ci_build_files: tuple[str, ...] = Field(max_length=30)
    documentation_files: tuple[str, ...] = Field(max_length=30)
    likely_entry_points: tuple[str, ...] = Field(max_length=30)
    exact_issue_paths: tuple[str, ...] = Field(max_length=20)
    filename_matches: tuple[str, ...] = Field(default=(), max_length=40)
    lexical_matches: tuple[LiteralMatch, ...] = Field(max_length=40)
    direct_reference_hints: tuple[str, ...] = Field(default=(), max_length=40)
    key_excerpts: tuple[RepositoryExcerpt, ...] = Field(max_length=12)
    omitted_sections: tuple[str, ...] = Field(default=(), max_length=20)


class RepositoryScout:
    """Read-only repository mapper with deterministic ranking and limits."""

    def __init__(
        self,
        *,
        workspace: Path,
        sandbox: Sandbox,
        max_output_chars: int,
        timeout_seconds: int,
    ) -> None:
        self._workspace = workspace
        self._sandbox = sandbox
        self._max_output_chars = max_output_chars
        self._timeout_seconds = timeout_seconds

    def scout(self, *, issue_text: str, base_sha: str) -> RepositoryMap:
        logger.info("V2 repository Scout started", extra={"base_sha": base_sha})
        inventory = tracked_inventory(self._workspace)
        paths = inventory.paths
        path_set = set(paths)
        exact_paths = tuple(
            candidate
            for candidate in _issue_paths(issue_text)
            if candidate in path_set
        )[:20]
        terms = _issue_terms(issue_text)
        folded_terms = tuple(term.casefold() for term in terms)
        filename_matches = tuple(
            path
            for path in paths
            if any(
                term in PurePosixPath(path).name.casefold()
                for term in folded_terms
            )
        )[:40]
        matches: list[LiteralMatch] = []
        for term in terms:
            remaining = 40 - len(matches)
            if remaining <= 0:
                break
            found = search_literal_matches(
                self._sandbox,
                query=term,
                max_results=min(5, remaining),
                timeout_seconds=self._timeout_seconds,
            )
            matches.extend(found)

        manifests = tuple(
            path for path in paths if PurePosixPath(path).name in _MANIFEST_NAMES
        )[:30]
        docs = tuple(path for path in paths if PurePosixPath(path).name in _DOC_NAMES)[:30]
        lexical_paths = tuple(item.path for item in matches)
        adjacent_tests = tuple(
            path
            for path in paths
            if any(part in {"test", "tests", "spec"} for part in PurePosixPath(path).parts)
            and any(
                PurePosixPath(candidate).stem.casefold()
                in PurePosixPath(path).stem.casefold()
                for candidate in (*exact_paths, *filename_matches, *lexical_paths)
            )
        )
        key_paths = tuple(
            dict.fromkeys(
                (
                    *exact_paths,
                    *filename_matches,
                    *lexical_paths,
                    *adjacent_tests,
                    *manifests,
                    *docs,
                )
            )
        )[:12]
        excerpts: list[RepositoryExcerpt] = []
        for path in key_paths:
            try:
                content = read_file(
                    self._workspace,
                    path=path,
                    start_line=1,
                    end_line=80,
                    max_output_chars=min(self._max_output_chars, 12_000),
                )
            except RepositoryError:
                continue
            excerpts.append(RepositoryExcerpt(path=path, content=content))

        top_levels = tuple(sorted({PurePosixPath(path).parts[0] for path in paths if path}))[:200]
        test_roots = tuple(
            sorted(
                {
                    path.split("/", 1)[0]
                    for path in paths
                    if any(
                        part in {"test", "tests", "spec", "specs"}
                        for part in PurePosixPath(path).parts
                    )
                }
            )
        )[:30]
        ci_files = tuple(
            path
            for path in paths
            if path.startswith(".github/workflows/")
            or PurePosixPath(path).name in {"Makefile", "tox.ini", "noxfile.py"}
        )[:30]
        entries = tuple(path for path in paths if PurePosixPath(path).name in _ENTRY_NAMES)[:30]
        languages = Counter(
            language
            for path in paths
            if (language := _LANGUAGE_EXTENSIONS.get(PurePosixPath(path).suffix.lower()))
        )
        omitted: list[str] = []
        if inventory.truncated:
            omitted.append("tracked_paths_sample")
        if len(matches) >= 40:
            omitted.append("lexical_matches")
        repository_map = RepositoryMap(
            base_sha=base_sha,
            tracked_file_count=inventory.total_count,
            tracked_paths_sample=paths,
            top_level_summary=top_levels,
            language_summary=dict(sorted(languages.items())),
            manifests=manifests,
            test_roots=test_roots,
            ci_build_files=ci_files,
            documentation_files=docs,
            likely_entry_points=entries,
            exact_issue_paths=exact_paths,
            filename_matches=filename_matches,
            lexical_matches=tuple(matches),
            direct_reference_hints=(),
            key_excerpts=tuple(excerpts),
            omitted_sections=tuple(omitted),
        )
        logger.info(
            "V2 repository Scout completed",
            extra={
                "tracked_file_count": repository_map.tracked_file_count,
                "lexical_match_count": len(repository_map.lexical_matches),
            },
        )
        return repository_map


def _issue_paths(issue_text: str) -> tuple[str, ...]:
    paths: list[str] = []
    for raw in _PATH_TOKEN.findall(issue_text.replace("`", "")):
        normalized = raw.strip(".,:;()[]{}")
        pure = PurePosixPath(normalized)
        if pure.is_absolute() or ".." in pure.parts or ".git" in pure.parts:
            continue
        if normalized not in paths:
            paths.append(normalized)
    return tuple(paths[:20])


def _issue_terms(issue_text: str) -> tuple[str, ...]:
    terms: list[str] = []
    for value in _IDENTIFIER.findall(issue_text):
        if value.casefold() in _STOP_WORDS:
            continue
        if value.casefold() in {item.casefold() for item in terms}:
            continue
        terms.append(value)
        if len(terms) >= 12:
            break
    return tuple(terms)
