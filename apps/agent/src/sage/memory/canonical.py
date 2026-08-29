"""Stable content identity for canonical SMRT objects."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from pydantic import BaseModel


def canonical_bytes(value: BaseModel | object) -> bytes:
    """Serialize a JSON-compatible value without database-specific behavior."""

    primitive = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    _reject_non_finite(primitive)
    return json.dumps(
        primitive,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def canonical_digest(value: BaseModel | object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def text_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _reject_non_finite(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Canonical JSON cannot contain non-finite numbers.")
    if isinstance(value, dict):
        for item in value.values():
            _reject_non_finite(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_non_finite(item)
