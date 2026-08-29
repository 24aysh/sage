import asyncio
from pathlib import Path
from uuid import uuid4

import pytest

from sage.errors import MemoryPolicyError
from sage.memory.models import (
    ContextExpansionRequest,
    DirectMaterializationRequest,
    MutationAuthorization,
    RepositoryIdentity,
)
from sage.memory.parsing import TreeSitterExtractor
from sage.memory.retrieval.sparse import SQLiteSparseIndex
from sage.memory.session import ActiveMemorySession


class _Repository:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    def read_file(self, *, path, start_line=1, end_line=None):
        lines = (self.workspace / path).read_text().splitlines()
        selected = lines[start_line - 1 : end_line or start_line + 299]
        return "\n".join(selected)

    def list_tree(self, *, path=".", max_depth=2):
        return f"{path}:{max_depth}"

    def search_text(self, *, query, path=".", max_results=50):
        return ""


class _Git:
    def __init__(self, sources: dict[str, str]) -> None:
        self.sources = sources

    def list_files(self, commit):
        return [
            (path, character * 40)
            for path, character in zip(self.sources, "bcd", strict=True)
        ]

    def read_blob(self, commit, path):
        character = {name: value for name, value in zip(self.sources, "bcd", strict=True)}[
            path
        ]
        return character * 40, self.sources[path]


class _Store:
    async def find_semantic_by_source(self, repository_id, *, source_oid):
        return None


class _Summarizer:
    provider_name = "fake"
    model_name = "fake"


def test_healthy_mutation_policy_requires_current_read_coverage(tmp_path) -> None:
    asyncio.run(_exercise_mutation_policy(tmp_path))


async def _exercise_mutation_policy(tmp_path) -> None:
    source = "\n".join(f"line {number}" for number in range(1, 402)) + "\n"
    (tmp_path / "notes.txt").write_text(source)
    (tmp_path / "dependency.md").write_text("dependency\n")
    (tmp_path / "extra.md").write_text("extra\n")
    sources = {
        "notes.txt": source,
        "dependency.md": "dependency\n",
        "extra.md": "extra\n",
    }
    index = SQLiteSparseIndex()
    session = ActiveMemorySession(
        repository=_Repository(tmp_path),
        identity=RepositoryIdentity(
            namespace_kind="local", namespace_key="repo", display_name="repo"
        ),
        repository_id=uuid4(),
        target_commit="a" * 40,
        workspace=tmp_path,
        git=_Git(sources),
        store=_Store(),
        summarizer=_Summarizer(),
        extractor=TreeSitterExtractor(),
        index=index,
        prior_documents=[],
        input_snapshot_id=None,
        building_snapshot_id=uuid4(),
        initial_max_files=8,
        expansion_max_files=6,
        context_chars=48_000,
        max_file_source_chars=120_000,
        beam_width=4,
        max_candidates_per_round=16,
        max_navigation_rounds=6,
    )
    try:
        forest = await session.initial_context("notes.txt")
        assert forest.paths == ("notes.txt",)
        with pytest.raises(MemoryPolicyError, match="expand_context"):
            await session.read_file(
                path="dependency.md", start_line=1, end_line=10
            )
        with pytest.raises(MemoryPolicyError, match="active source path"):
            await session.materialize_dependency(
                DirectMaterializationRequest(
                    path="dependency.md", reason="Needed for implementation"
                )
            )
        assert (
            await session.materialize_dependency(
                DirectMaterializationRequest(
                    path="dependency.md", reason="Referenced by notes.txt line 1"
                )
            )
            == "dependency"
        )
        with pytest.raises(MemoryPolicyError, match="active file or directory"):
            await session.search_text(query="line", path=".", max_results=10)
        assert await session.search_text(
            query="line", path="notes.txt", max_results=10
        ) == ""
        delta = await session.expand(
            ContextExpansionRequest(query="extra.md", reason="Verify another path")
        )
        assert delta.paths == ("extra.md",)
        repeated = await session.expand(
            ContextExpansionRequest(query="extra.md", reason="Verify another path")
        )
        assert repeated.paths == ()
        assert "deterministic_dependency" in session.inspect_context()

        session.record_mutation("notes.txt")
        await session.read_file(path="notes.txt", start_line=1, end_line=10)
        session.authorize_mutation(
            MutationAuthorization(
                operation="replace", path="notes.txt", old_text="line 5\n"
            )
        )
        with pytest.raises(MemoryPolicyError, match="Full file coverage"):
            session.authorize_mutation(
                MutationAuthorization(
                    operation="write",
                    path="notes.txt",
                    replacing_entire_file=True,
                )
            )
        with pytest.raises(MemoryPolicyError, match="outside"):
            session.authorize_mutation(
                MutationAuthorization(
                    operation="replace", path="notes.txt", old_text="line 350\n"
                )
            )
        with pytest.raises(MemoryPolicyError, match="depth one"):
            await session.list_tree(path=".", max_depth=2)
        session.record_mutation("notes.txt")
        with pytest.raises(MemoryPolicyError, match="current file source"):
            session.authorize_mutation(
                MutationAuthorization(
                    operation="replace", path="notes.txt", old_text="line 5\n"
                )
            )
        await session.read_file(path="notes.txt", start_line=1, end_line=10)
    finally:
        index.close()
