"""Shared bounded current-source reads."""

from collections.abc import Callable

from sage.errors import RepositoryError


def source_snippets(nodes: list[dict], reader: Callable[..., str]) -> list[dict]:
    """Use the source-read boundary; never open graph-supplied paths directly."""
    result = []
    remaining = 6000
    for node in nodes[:5]:
        if remaining < 200:
            break
        start = int(node["line_start"])
        try:
            source = reader(path=node["file_path"], start_line=start,
                            end_line=min(start + 49, int(node["line_end"])))
        except RepositoryError:
            source = "Source read unavailable; inspect the current path separately."
        result.append({"path": node["file_path"], "start_line": start,
                       "source": source[:remaining], "truncated": len(source) > remaining})
        remaining -= min(len(source), remaining)
    return result
