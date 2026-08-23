from pathlib import Path

import pytest

from sage.artifacts.v2 import V2ArtifactStore
from sage.domain.usage import RunProvenance
from sage.errors import ArtifactError


def test_v2_artifact_store_writes_fixed_atomic_artifacts(tmp_path: Path) -> None:
    store = V2ArtifactStore(tmp_path)

    usage_path = store.write_usage(RunProvenance())
    context_path = store.write_context("planner", 1, "bounded packet")

    assert usage_path == tmp_path / "usage.json"
    assert context_path == tmp_path / "contexts/01-planner.txt"
    assert "constrained-cross-provider" in usage_path.read_text()


def test_v2_artifact_stage_names_cannot_escape_run_directory(tmp_path: Path) -> None:
    store = V2ArtifactStore(tmp_path)

    with pytest.raises(ArtifactError, match="stage name"):
        store.write_context("../../outside", 1, "no")

    assert list(tmp_path.iterdir()) == []
