import asyncio
import logging
from pathlib import Path
from uuid import uuid4

import pytest

from sage.errors import MemoryIntegrityError, MemoryPolicyError
from sage.memory.models import (
    ContextExpansionRequest,
    DirectMaterializationRequest,
    FileSemanticPayload,
    MemoryMode,
    MutationAuthorization,
    RepositoryIdentity,
)
from sage.memory.parsing import TreeSitterExtractor
from sage.memory.retrieval.sparse import SQLiteSparseIndex
from sage.memory.session import ActiveMemorySession
from sage.providers.errors import ProviderErrorCategory, ProviderInvocationError


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
        self.oids = {
            path: f"{index:040x}"
            for index, path in enumerate(sources, start=1)
        }

    def list_files(self, commit):
        return list(self.oids.items())

    def read_blob(self, commit, path):
        return self.oids[path], self.sources[path]


class _Store:
    def __init__(self) -> None:
        self.semantic = []
        self.failed = []

    async def find_semantic_by_source(
        self, repository_id, *, source_oid, **semantic_identity
    ):
        return None

    async def insert_semantic_object(self, repository_id, semantic):
        self.semantic.append(semantic)

    async def mark_snapshot_failed(self, snapshot_id, *, failure_code):
        self.failed.append((snapshot_id, failure_code))
        self.semantic.clear()


class _Summarizer:
    provider_name = "fake"
    model_name = "fake"

    async def summarize_file(self, *, path, source, structure):
        return FileSemanticPayload(summary=f"Semantic card for {path}")


class _FailSecondSummarizer(_Summarizer):
    def __init__(self) -> None:
        self.calls = 0

    async def summarize_file(self, *, path, source, structure):
        self.calls += 1
        if self.calls == 2:
            provider_error = ProviderInvocationError(
                ProviderErrorCategory.RATE_LIMITED,
                provider="google",
                model="gemini-test",
                status_code=429,
                retryable=True,
            )
            raise MemoryIntegrityError(
                "Semantic summarization failed safely."
            ) from provider_error
        return await super().summarize_file(
            path=path, source=source, structure=structure
        )


def test_healthy_mutation_policy_requires_current_read_coverage(
    tmp_path, caplog
) -> None:
    caplog.set_level(logging.DEBUG, logger="sage.memory.session")
    asyncio.run(_exercise_mutation_policy(tmp_path))

    assert "memory context materialized path='notes.txt'" in caplog.text
    assert (
        "memory source read access=materialize_dependency mode=healthy "
        "path='dependency.md' lines=1-1"
    ) in caplog.text
    assert "memory tree accessed path='.' max_depth=1" in caplog.text
    assert "memory text search accessed scope='project/tests'" in caplog.text
    assert (
        "memory source read access=read_file mode=healthy path='notes.txt' "
        "lines=1-10"
    ) in caplog.text
    assert "def factorial(): ..." not in caplog.text


def test_learning_failure_aborts_snapshot_and_discards_partial_write(
    tmp_path,
) -> None:
    asyncio.run(_exercise_learning_failure(tmp_path))


async def _exercise_learning_failure(tmp_path) -> None:
    sources = {
        "src/alpha.py": "def alpha():\n    pass\n",
        "tests/beta.py": "def beta():\n    pass\n",
    }
    for path, source in sources.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source)
    store = _Store()
    snapshot_id = uuid4()
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
        store=store,
        summarizer=_FailSecondSummarizer(),
        extractor=TreeSitterExtractor(),
        index=index,
        prior_documents=[],
        input_snapshot_id=None,
        building_snapshot_id=snapshot_id,
        initial_max_files=8,
        expansion_max_files=6,
        context_chars=48_000,
        max_file_source_chars=120_000,
        beam_width=4,
        max_candidates_per_round=16,
        max_navigation_rounds=6,
    )
    try:
        await session.initial_context("src/alpha.py tests/beta.py")
        assert session.mode is MemoryMode.FALLBACK
        assert store.failed == [(snapshot_id, "rate_limited")]
        assert store.semantic == []
    finally:
        index.close()


async def _exercise_mutation_policy(tmp_path) -> None:
    source = "\n".join(f"line {number}" for number in range(1, 402)) + "\n"
    (tmp_path / "notes.txt").write_text(source)
    (tmp_path / "dependency.md").write_text("dependency\n")
    (tmp_path / "extra.md").write_text("extra\n")
    (tmp_path / "project/src/factorial").mkdir(parents=True)
    (tmp_path / "project/tests").mkdir(parents=True)
    (tmp_path / "project/src/factorial/main.py").write_text(
        "def factorial(): ...\n"
    )
    (tmp_path / "project/tests/test_existing.py").write_text(
        "def test_existing(): ...\n"
    )
    sources = {
        "notes.txt": source,
        "dependency.md": "dependency\n",
        "extra.md": "extra\n",
        "project/src/factorial/main.py": "def factorial(): ...\n",
        "project/tests/test_existing.py": "def test_existing(): ...\n",
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
        with pytest.raises(MemoryPolicyError, match="active or listed directory"):
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
        assert await session.list_tree(path=".", max_depth=1) == ".:1"
        with pytest.raises(MemoryPolicyError, match="active context"):
            await session.list_tree(
                path="project/src/factorial", max_depth=1
            )
        assert await session.search_text(
            query="test", path="project/tests", max_results=10
        ) == ""
        assert await session.list_tree(
            path="project/src", max_depth=1
        ) == "project/src:1"
        assert (
            await session.materialize_dependency(
                DirectMaterializationRequest(
                    path="project/src/factorial/main.py",
                    reason=(
                        "Inspect project/src/factorial/main.py found beneath "
                        "the listed project/src/factorial directory"
                    ),
                )
            )
            == "def factorial(): ..."
        )
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
