import math

import pytest

from sage.memory.canonical import canonical_bytes, canonical_digest


def test_canonical_digest_is_key_order_independent_and_list_order_sensitive() -> None:
    first = {"name": "Sage", "items": ["a", "b"]}
    reordered_keys = {"items": ["a", "b"], "name": "Sage"}

    assert canonical_digest(first) == canonical_digest(reordered_keys)
    assert canonical_digest(first) != canonical_digest(
        {"name": "Sage", "items": ["b", "a"]}
    )
    assert canonical_digest(first) == (
        "811ae2cad64affae6a61e3a5c1c6c8ab63f5dc8e64bc2228850ec5c65e50799b"
    )


def test_canonical_json_preserves_unicode_and_rejects_non_finite_values() -> None:
    assert " स्मृति".encode() in canonical_bytes({"value": " स्मृति"})
    with pytest.raises(ValueError, match="non-finite"):
        canonical_digest({"value": math.inf})
