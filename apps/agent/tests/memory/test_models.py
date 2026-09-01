import pytest
from pydantic import ValidationError

from sage.memory.models import (
    DirectorySemanticPayload,
    FileSemanticPayload,
    FileStructure,
    NodeType,
    SemanticObject,
)

_DIGEST = "a" * 64
_OID = "b" * 40


def test_semantic_payloads_normalize_duplicate_bounded_items() -> None:
    payload = FileSemanticPayload(
        summary="Does one thing",
        responsibilities=[" parse   files ", "parse files", ""],
        concepts=["memory"],
    )

    assert payload.responsibilities == ["parse files"]


def test_file_and_directory_payloads_cannot_cross_node_boundaries() -> None:
    structure = FileStructure(
        language="python",
        parser_version="test",
        parse_status="parsed",
    )
    with pytest.raises(ValidationError, match="file semantic payload"):
        SemanticObject(
            semantic_digest=_DIGEST,
            payload_digest=_DIGEST,
            node_type=NodeType.FILE,
            source_oid=_OID,
            semantic_payload=DirectorySemanticPayload(summary="directory"),
            structure=structure,
            summarizer_provider="fake",
            summarizer_model="fake",
            prompt_version="v1",
        )


def test_file_semantic_object_requires_structure() -> None:
    with pytest.raises(ValidationError, match="structure"):
        SemanticObject(
            semantic_digest=_DIGEST,
            payload_digest=_DIGEST,
            node_type=NodeType.FILE,
            source_oid=_OID,
            semantic_payload=FileSemanticPayload(summary="file"),
            summarizer_provider="fake",
            summarizer_model="fake",
            prompt_version="v1",
        )
