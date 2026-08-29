import asyncio
from pathlib import Path
from uuid import uuid4

from sage.memory.git_state import GitStateReader
from sage.memory.models import (
    DirectorySemanticPayload,
    FileSemanticPayload,
    FileStructure,
    NodeType,
    SearchDocument,
    SemanticState,
)
from sage.memory.snapshots import build_sparse_overlay
from sage.memory.summarizer import build_file_semantic_object
from sage.repository.host_git import run_git


class _Store:
    def __init__(self) -> None:
        self.semantic = []

    async def insert_semantic_object(self, repository_id, value) -> None:
        self.semantic.append((repository_id, value))

    async def find_semantic_by_source(self, repository_id, **kwargs):
        return None


class _Summarizer:
    provider_name = "fake"
    model_name = "fake-v1"

    async def summarize_directory(self, *, path, children):
        return DirectorySemanticPayload(
            summary=f"Directory {path}",
            responsibilities=[f"Contains {len(children)} known children"],
        )


class _DeltaSummarizer(_Summarizer):
    def __init__(self) -> None:
        self.delta_calls = 0
        self.full_calls = 0

    async def summarize_directory(self, *, path, children):
        self.full_calls += 1
        return await super().summarize_directory(path=path, children=children)

    async def summarize_directory_delta(
        self, *, path, previous, changed_children, removed_children
    ):
        self.delta_calls += 1
        assert previous.summary == "Previous root"
        assert [name for name, _payload in changed_children] == ["engine.py"]
        assert removed_children == []
        return DirectorySemanticPayload(summary="Delta root")


class _StaticGit:
    def list_files(self, commit):
        return [("engine.py", "c" * 40)]

    def object_oid(self, commit, path):
        assert path == "."
        return "f" * 40


def _git(repository: Path, arguments: list[str]) -> str:
    result = run_git(arguments, repository=repository)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_sparse_overlay_contains_only_learned_path_and_ancestors(tmp_path) -> None:
    asyncio.run(_exercise_sparse_overlay(tmp_path))


def test_parent_refresh_uses_configured_delta_thresholds() -> None:
    asyncio.run(_exercise_parent_delta())


async def _exercise_parent_delta() -> None:
    structure = FileStructure(
        language="python",
        symbols=["run"],
        parser_version="test",
        parse_status="parsed",
    )
    previous_file = build_file_semantic_object(
        source_oid="b" * 40,
        payload=FileSemanticPayload(summary="Previous file"),
        structure=structure,
        provider="fake",
        model="fake-v1",
    )
    current_file = build_file_semantic_object(
        source_oid="c" * 40,
        payload=FileSemanticPayload(summary="Current file"),
        structure=structure,
        provider="fake",
        model="fake-v1",
    )
    prior = [
        SearchDocument(
            path="engine.py",
            node_type=NodeType.FILE,
            source_oid=previous_file.source_oid,
            semantic_digest=previous_file.semantic_digest,
            payload_digest=previous_file.payload_digest,
            summary="Previous file",
            semantic_state=SemanticState.VALID,
        ),
        SearchDocument(
            path=".",
            node_type=NodeType.DIRECTORY,
            source_oid="e" * 40,
            semantic_digest="d" * 64,
            payload_digest="e" * 64,
            summary="Previous root",
            derived_from=(("engine.py", previous_file.semantic_digest),),
            semantic_state=SemanticState.VALID,
        ),
    ]
    summarizer = _DeltaSummarizer()
    store = _Store()
    await build_sparse_overlay(
        repository_id=uuid4(),
        commit_oid="a" * 40,
        git=_StaticGit(),
        store=store,
        summarizer=summarizer,
        prior_documents=prior,
        learned={"engine.py": current_file},
        parent_delta_limit=3,
        parent_changed_child_limit=4,
    )
    directory = store.semantic[-1][1]
    assert summarizer.delta_calls == 1
    assert summarizer.full_calls == 0
    assert directory.generation_mode == "delta"
    assert directory.delta_depth == 1

    full_summarizer = _DeltaSummarizer()
    full_store = _Store()
    await build_sparse_overlay(
        repository_id=uuid4(),
        commit_oid="a" * 40,
        git=_StaticGit(),
        store=full_store,
        summarizer=full_summarizer,
        prior_documents=prior,
        learned={"engine.py": current_file},
        parent_delta_limit=0,
        parent_changed_child_limit=4,
    )
    assert full_summarizer.delta_calls == 0
    assert full_summarizer.full_calls == 1
    assert full_store.semantic[-1][1].generation_mode == "full"

    same_meaning_file = build_file_semantic_object(
        source_oid="c" * 40,
        payload=FileSemanticPayload(summary="Previous file"),
        structure=structure,
        provider="fake",
        model="fake-v1",
    )
    unchanged_summarizer = _DeltaSummarizer()
    unchanged_store = _Store()
    await build_sparse_overlay(
        repository_id=uuid4(),
        commit_oid="a" * 40,
        git=_StaticGit(),
        store=unchanged_store,
        summarizer=unchanged_summarizer,
        prior_documents=prior,
        learned={"engine.py": same_meaning_file},
        parent_delta_limit=3,
        parent_changed_child_limit=4,
    )
    unchanged_directory = unchanged_store.semantic[-1][1]
    assert unchanged_summarizer.delta_calls == 0
    assert unchanged_summarizer.full_calls == 0
    assert unchanged_directory.semantic_payload.summary == "Previous root"


async def _exercise_sparse_overlay(tmp_path) -> None:
    repository = tmp_path / "repo"
    (repository / "src").mkdir(parents=True)
    _git(repository, ["init"])
    _git(repository, ["config", "user.name", "Test"])
    _git(repository, ["config", "user.email", "test@example.com"])
    (repository / "src" / "engine.py").write_text("def run():\n    pass\n")
    (repository / "unknown.py").write_text("VALUE = 1\n")
    _git(repository, ["add", "."])
    _git(repository, ["commit", "-m", "base"])
    commit = _git(repository, ["rev-parse", "HEAD"])
    git = GitStateReader(repository)
    oid, _ = git.read_blob(commit, "src/engine.py")
    semantic = build_file_semantic_object(
        source_oid=oid,
        payload=FileSemanticPayload(summary="Runs the engine"),
        structure=FileStructure(
            language="python",
            symbols=["run"],
            parser_version="test",
            parse_status="parsed",
        ),
        provider="fake",
        model="fake-v1",
    )
    store = _Store()

    root, nodes, reused, stale = await build_sparse_overlay(
        repository_id=uuid4(),
        commit_oid=commit,
        git=git,
        store=store,
        summarizer=_Summarizer(),
        prior_documents=[],
        learned={"src/engine.py": semantic},
        parent_delta_limit=3,
        parent_changed_child_limit=4,
    )

    assert root == nodes[-1].overlay_digest
    assert {node.node_type for node in nodes} == {NodeType.FILE, NodeType.DIRECTORY}
    assert len(nodes) == 3
    assert all("unknown.py" not in str(node.children) for node in nodes)
    assert nodes[0].semantic_state is SemanticState.VALID
    assert reused == stale == 0
    assert len(store.semantic) == 2

    (repository / "src" / "engine.py").write_text("def run():\n    return 2\n")
    _git(repository, ["add", "src/engine.py"])
    _git(repository, ["commit", "-m", "change known file"])
    changed_commit = _git(repository, ["rev-parse", "HEAD"])
    _root, changed_nodes, _reused, changed_stale = await build_sparse_overlay(
        repository_id=uuid4(),
        commit_oid=changed_commit,
        git=git,
        store=store,
        summarizer=_Summarizer(),
        prior_documents=[
            SearchDocument(
                path="src/engine.py",
                node_type=NodeType.FILE,
                source_oid=oid,
                semantic_digest=semantic.semantic_digest,
                summary="Runs the engine",
                semantic_state=SemanticState.VALID,
            )
        ],
        learned={},
        parent_delta_limit=3,
        parent_changed_child_limit=4,
    )
    changed_file = next(
        node for node in changed_nodes if node.node_type is NodeType.FILE
    )
    assert changed_file.semantic_state is SemanticState.STALE
    assert changed_file.semantic_digest is None
    assert changed_file.stale_hint_digest == semantic.semantic_digest
    assert changed_stale == 1

    _git(repository, ["checkout", "-b", "rename-case", commit])
    _git(repository, ["mv", "src/engine.py", "src/runner.py"])
    _git(repository, ["commit", "-m", "rename known file"])
    rename_commit = _git(repository, ["rev-parse", "HEAD"])
    _root, renamed_nodes, renamed_reused, _stale = await build_sparse_overlay(
        repository_id=uuid4(),
        commit_oid=rename_commit,
        git=git,
        store=store,
        summarizer=_Summarizer(),
        prior_documents=[
            SearchDocument(
                path="src/engine.py",
                node_type=NodeType.FILE,
                source_oid=oid,
                semantic_digest=semantic.semantic_digest,
                summary="Runs the engine",
                semantic_state=SemanticState.VALID,
            )
        ],
        learned={},
        parent_delta_limit=3,
        parent_changed_child_limit=4,
    )
    renamed_file = next(
        node for node in renamed_nodes if node.node_type is NodeType.FILE
    )
    assert renamed_file.source_oid == oid
    assert renamed_file.semantic_digest == semantic.semantic_digest
    assert renamed_file.semantic_state is SemanticState.VALID
    assert renamed_reused >= 1
