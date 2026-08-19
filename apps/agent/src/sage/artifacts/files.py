"""Atomic plain-filesystem artifact writes."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4


def write_json_atomic(path: Path, value: object) -> None:
    """Serialize JSON deterministically and replace the destination atomically."""

    write_text_atomic(path, f"{json.dumps(value, indent=2, sort_keys=True)}\n")


def write_text_atomic(path: Path, value: str) -> None:
    """Replace a UTF-8 text file without exposing a partially written result."""

    temporary_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary_path.write_text(value, encoding="utf-8")
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)
