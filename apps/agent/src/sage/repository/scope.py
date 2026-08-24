"""Deterministic write-scope validation for V2 candidates."""

from __future__ import annotations

import fnmatch
from pathlib import PurePosixPath

from sage.errors import RepositoryError


def validate_write_scopes(scopes: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """Normalize and validate safe relative exact/glob scopes."""

    return _normalize_scopes(scopes, allow_protected=False)


def _normalize_scopes(
    scopes: tuple[str, ...] | list[str],
    *,
    allow_protected: bool,
) -> tuple[str, ...]:
    if not scopes:
        raise RepositoryError("At least one allowed write scope is required.")
    normalized: list[str] = []
    for value in scopes:
        scope = value.strip().replace("\\", "/")
        pure = PurePosixPath(scope)
        if (
            not scope
            or pure.is_absolute()
            or ".." in pure.parts
            or (not allow_protected and ".git" in pure.parts)
            or scope.startswith("/")
        ):
            raise RepositoryError(f"Unsafe V2 write scope: {value}")
        if scope not in normalized:
            normalized.append(scope)
    return tuple(normalized)


def paths_outside_scopes(
    paths: list[str] | tuple[str, ...],
    *,
    allowed_scopes: tuple[str, ...],
    forbidden_scopes: tuple[str, ...] = (".git/**", ".sage/runs/**"),
) -> tuple[str, ...]:
    """Return Git-derived paths that violate frozen scope policy."""

    allowed = validate_write_scopes(allowed_scopes)
    forbidden = _normalize_scopes(forbidden_scopes, allow_protected=True)
    violations: list[str] = []
    for raw_path in paths:
        path = raw_path.replace("\\", "/")
        pure = PurePosixPath(path)
        unsafe = pure.is_absolute() or ".." in pure.parts or ".git" in pure.parts
        blocked = any(_matches(path, pattern) for pattern in forbidden)
        admitted = any(_matches(path, pattern) for pattern in allowed)
        if unsafe or blocked or not admitted:
            violations.append(raw_path)
    return tuple(sorted(set(violations)))


def _matches(path: str, pattern: str) -> bool:
    if pattern.endswith("/**"):
        root = pattern[:-3].rstrip("/")
        if path == root or path.startswith(f"{root}/"):
            return True
    return fnmatch.fnmatchcase(path, pattern)
