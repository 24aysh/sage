"""Saved-diff normalization used by the offline publication smoke test."""

from __future__ import annotations

import logging
import shlex

logger = logging.getLogger(__name__)


def normalize_null_file_headers(patch: str) -> str:
    """Canonicalize unambiguous variants of the null file header."""

    normalized = patch.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.splitlines(keepends=True)
    for index in range(len(lines) - 1):
        source = _parse_file_header(lines[index], prefix="--- ")
        target = _parse_file_header(lines[index + 1], prefix="+++ ")
        if source is None or target is None:
            continue
        source_is_null = _is_null_header_alias(source[0])
        target_is_null = _is_null_header_alias(target[0])
        if source_is_null == target_is_null:
            continue
        if source_is_null:
            lines[index] = _render_null_file_header("--- ", source[1], source[2])
        else:
            lines[index + 1] = _render_null_file_header(
                "+++ ",
                target[1],
                target[2],
            )
    rendered = "".join(lines)
    if rendered != normalized:
        changed_headers = sum(
            before != after
            for before, after in zip(
                normalized.splitlines(),
                rendered.splitlines(),
                strict=True,
            )
        )
        logger.info(
            "Publication smoke: normalized null file headers count=%d",
            changed_headers,
        )
    return rendered


def _parse_file_header(
    raw_line: str,
    *,
    prefix: str,
) -> tuple[str, str, str] | None:
    line = raw_line.removesuffix("\n")
    if not line.startswith(prefix):
        return None
    value, separator, metadata = line[len(prefix) :].partition("\t")
    try:
        parsed = shlex.split(value.strip())
    except ValueError:
        return None
    if len(parsed) != 1:
        return None
    ending = "\n" if raw_line.endswith("\n") else ""
    return parsed[0], f"\t{metadata}" if separator else "", ending


def _is_null_header_alias(value: str) -> bool:
    return value in {"/dev/null", "dev/null", "a/dev/null", "b/dev/null"}


def _render_null_file_header(prefix: str, metadata: str, ending: str) -> str:
    return f"{prefix}/dev/null{metadata}{ending}"
