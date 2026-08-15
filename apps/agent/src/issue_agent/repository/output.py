"""Shared output-boundary helpers for repository tools."""


def truncate_text(value: str, max_chars: int) -> str:
    """Bound text while retaining useful context from both ends."""

    if len(value) <= max_chars:
        return value

    omitted = len(value) - max_chars
    marker = f"\n... [truncated approximately {omitted} characters] ...\n"
    available = max_chars - len(marker)
    if available <= 0:
        return marker[:max_chars]

    head_size = (available + 1) // 2
    tail_size = available - head_size
    tail = value[-tail_size:] if tail_size else ""
    return f"{value[:head_size]}{marker}{tail}"
