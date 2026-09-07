"""Offline Gemini-boundary and real local-Qdrant lifecycle coverage."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from sage.config import LegionEmbeddingSettings
from sage.domain.embeddings import EmbeddingIdentity, MemoryVectorError, VectorPoint, VectorUsage, validate_vector
from sage.domain.memory import MemoryRetrievalStatus
from sage.errors import ConfigurationError
from sage.integrations.qdrant import QdrantVectorStore
from sage.legion_memory.service import LegionMemoryService
from sage.legion_memory.store import GraphStore
from sage.legion_memory.vectors import VectorIndex, memory_lock, node_text
from sage.legion_memory.search import rrf_merge
from sage.providers.embeddings import GeminiEmbeddingProvider
from .conftest import commit_all


class FakeEmbeddings:
    identity = EmbeddingIdentity(dimensions=3)

    def __init__(self):
        self.usage = VectorUsage()
        self.fail = False
        self.texts = []

    def embed(self, text, *, query, timeout):
        assert 0 < timeout <= 30
        self.texts.append(text)
        if self.fail:
            raise MemoryVectorError("Simulated embedding failure.")
        if query:
            self.usage.query_calls += 1
            return [0., 0., 1.] if "unrelated" in text else [1., 0., 0.]
        self.usage.document_calls += 1
        return [1., 0., 0.] if "helper" in text else [0., 1., 0.]


def vector_service(tmp_path):
    provider = FakeEmbeddings()

    def factory(database, collection, create):
        return QdrantVectorStore(path=tmp_path / "vectors", collection=collection,
            dimensions=3, usage=provider.usage, create=create)

    return LegionMemoryService(vectors=VectorIndex(provider, factory)), provider


def test_build_reuses_vectors_and_semantic_only_issue_finds_code(fixture_repo, tmp_path):
    service, provider = vector_service(tmp_path)
    database = tmp_path / "graph.sqlite3"
    first = service.build_or_update_graph_tool(repo_root=fixture_repo, memory_file=database)
    assert first.vectors.status == "ready"
    assert first.vectors.embedded > 0
    assert all(text.startswith("title: ") for text in provider.texts)
    calls = provider.usage.document_calls
    second = service.build_or_update_graph_tool(repo_root=fixture_repo, memory_file=database)
    assert second.build_type == "no_change"
    assert second.vectors.embedded == 0
    assert second.vectors.reused == first.vectors.embedded
    assert provider.usage.document_calls == calls
    query = "Reimburse the buyer after revocation"
    result = service.retrieve_issue_context(issue_text=query, repo_root=fixture_repo, memory_file=database)
    assert result.status is MemoryRetrievalStatus.USED
    assert result.semantic_candidates > 0
    assert "semantic" in result.search_modes
    assert any(item.name == "helper" for item in result.items)
    assert result.context_chars == len(result.context)
    assert "signature:" in result.context
    assert "CALLS link to" in result.context or "TESTED_BY link to" in result.context
    service.retrieve_issue_context(issue_text=query, repo_root=fixture_repo, memory_file=database)
    assert provider.usage.query_calls == 1
    assert provider.usage.input_tokens is None


def test_semantic_no_hit_and_failed_index_preserve_lexical(fixture_repo, tmp_path):
    service, provider = vector_service(tmp_path)
    database = tmp_path / "graph.sqlite3"
    provider.fail = True
    build = service.build_or_update_graph_tool(repo_root=fixture_repo, memory_file=database)
    assert build.vectors.status == "unavailable"
    assert service.graph_stats(repo_root=fixture_repo, memory_file=database).status == "ready"
    result = service.retrieve_issue_context(issue_text="helper", repo_root=fixture_repo, memory_file=database)
    assert result.status == "used"
    assert result.vectors.status == "unavailable"
    assert result.warnings
    provider.fail = False
    build = service.build_or_update_graph_tool(repo_root=fixture_repo, memory_file=database)
    assert build.build_type == "no_change"
    assert build.vectors.status == "ready"
    result = service.retrieve_issue_context(issue_text="unrelated galactic penguins", repo_root=fixture_repo, memory_file=database)
    assert result.status == "no_match"


def test_changed_docstring_reembeds_only_changed_node_and_deleted_nodes_do_not_return(fixture_repo, tmp_path):
    service, provider = vector_service(tmp_path)
    database = tmp_path / "graph.sqlite3"
    first = service.build_or_update_graph_tool(repo_root=fixture_repo, memory_file=database)
    source = fixture_repo / "service.py"
    source.write_text(source.read_text().replace("def helper():", 'def helper():\n    """Refund the buyer."""'))
    commit_all(fixture_repo, "change one docstring")
    stale = service.retrieve_issue_context(issue_text="helper", repo_root=fixture_repo, memory_file=database)
    assert stale.status == "unavailable"
    second = service.build_or_update_graph_tool(repo_root=fixture_repo, memory_file=database)
    assert second.vectors.embedded == 1
    assert second.vectors.reused == first.vectors.embedded - 1
    source.write_text("def replacement():\n    return 42\n")
    commit_all(fixture_repo, "replace service")
    service.build_or_update_graph_tool(repo_root=fixture_repo, memory_file=database)
    result = service.semantic_search_nodes_tool(query="reimburse", repo_root=fixture_repo, memory_file=database)
    assert all(n["qualified_name"] != "service.py::helper" for n in result["data"]["nodes"])


def test_partial_failure_is_unpublished_and_restart_reuses_acknowledged_batches(fixture_repo, tmp_path):
    source = fixture_repo / "many.py"
    source.write_text("\n".join(f"def work_{n}():\n    pass\n" for n in range(40)))
    commit_all(fixture_repo, "many symbols")
    service, provider = vector_service(tmp_path)
    original = provider.embed

    def fail_later(text, **kwargs):
        if provider.usage.document_calls >= 34:
            raise MemoryVectorError("Interrupted batch.")
        return original(text, **kwargs)

    provider.embed = fail_later
    database = tmp_path / "graph.sqlite3"
    failed = service.build_or_update_graph_tool(repo_root=fixture_repo, memory_file=database)
    assert failed.vectors.status == "unavailable"
    with GraphStore(database, read_only=True) as store:
        assert store.get_metadata(f"vectors:{provider.identity.fingerprint}") == "{}"
    provider.embed = original
    repaired = service.build_or_update_graph_tool(repo_root=fixture_repo, memory_file=database)
    assert repaired.vectors.status == "ready"
    assert repaired.vectors.reused >= 32


def test_index_identity_and_lock_boundaries(fixture_repo, tmp_path):
    service, provider = vector_service(tmp_path)
    database = tmp_path / "graph.sqlite3"
    service.build_or_update_graph_tool(repo_root=fixture_repo, memory_file=database)
    provider.identity = provider.identity.model_copy(update={"recipe": "changed"})
    result = service.retrieve_issue_context(issue_text="helper", repo_root=fixture_repo, memory_file=database)
    assert result.vectors.status == "unavailable"
    with memory_lock(database):
        with pytest.raises(MemoryVectorError, match="busy"):
            with memory_lock(database):
                pass


def test_query_failures_and_invalid_manifest_keep_lexical_retrieval(fixture_repo, tmp_path):
    service, provider = vector_service(tmp_path)
    database = tmp_path / "graph.sqlite3"
    service.build_or_update_graph_tool(repo_root=fixture_repo, memory_file=database)
    provider.fail = True
    result = service.retrieve_issue_context(issue_text="helper", repo_root=fixture_repo, memory_file=database)
    assert result.status == "used"
    assert result.vectors.status == "unavailable"
    provider.fail = False
    # Failed query closes local Qdrant: the next query can acquire its lock.
    assert service.retrieve_issue_context(issue_text="helper", repo_root=fixture_repo, memory_file=database).vectors.status == "ready"
    with GraphStore(database) as store:
        with store.transaction():
            store.set_metadata(f"vectors:{provider.identity.fingerprint}", "[]")
    result = service.retrieve_issue_context(issue_text="helper", repo_root=fixture_repo, memory_file=database)
    assert result.status == "used"
    assert result.vectors.status == "unavailable"


def test_missing_collection_can_be_rebuilt_without_reusing_missing_vectors(fixture_repo, tmp_path):
    service, provider = vector_service(tmp_path)
    database = tmp_path / "graph.sqlite3"
    original = service.build_or_update_graph_tool(repo_root=fixture_repo, memory_file=database)

    def fresh_storage(database, collection, create):
        return QdrantVectorStore(path=tmp_path / "fresh-vectors", collection=collection,
            dimensions=3, usage=provider.usage, create=create)

    service.vectors = VectorIndex(provider, fresh_storage)
    result = service.retrieve_issue_context(issue_text="helper", repo_root=fixture_repo, memory_file=database)
    assert result.status == "used"
    assert result.vectors.status == "unavailable"
    rebuilt = service.build_or_update_graph_tool(repo_root=fixture_repo, memory_file=database)
    assert rebuilt.vectors.status == "ready"
    assert rebuilt.vectors.embedded == original.vectors.embedded
    assert rebuilt.vectors.reused == 0


def test_corrupt_cached_payload_is_replaced_before_publication(fixture_repo, tmp_path):
    service, provider = vector_service(tmp_path)
    database = tmp_path / "graph.sqlite3"
    first = service.build_or_update_graph_tool(repo_root=fixture_repo, memory_file=database)
    with GraphStore(database, read_only=True) as store:
        row = store.rows("SELECT * FROM vector_nodes LIMIT 1")[0]
        vectors = service.vectors._factory(database, service.vectors._collection(store), False)
        try:
            vectors.upsert([VectorPoint(row["point_id"], row["qualified_name"],
                "invalid-hash", [1., 0., 0.], row["generation"])])
        finally:
            vectors.close()
    repaired = service.build_or_update_graph_tool(repo_root=fixture_repo, memory_file=database)
    assert repaired.vectors.status == "ready"
    assert repaired.vectors.embedded == 1
    assert repaired.vectors.reused == first.vectors.embedded - 1


def test_node_and_deadline_budgets_do_not_break_the_graph(fixture_repo, tmp_path):
    service, provider = vector_service(tmp_path)
    database = tmp_path / "graph.sqlite3"
    service.vectors._max_nodes = 1
    result = service.build_or_update_graph_tool(repo_root=fixture_repo, memory_file=database)
    assert result.vectors.status == "unavailable"
    assert "node budget" in result.vectors.reason
    assert provider.usage.document_calls == 0
    service.vectors._max_nodes = 2000
    service.vectors._deadline = 0
    result = service.build_or_update_graph_tool(repo_root=fixture_repo, memory_file=database)
    assert result.vectors.status == "unavailable"
    assert "deadline" in result.vectors.reason
    assert provider.usage.document_calls == 0
    assert service.graph_stats(repo_root=fixture_repo, memory_file=database).status == "ready"


def test_rrf_is_rank_based_and_deterministic():
    assert rrf_merge([("a", 999), ("b", 1)], [("b", 0.8), ("c", 0.7)])[0][0] == "b"
    assert rrf_merge([("a", 0.1)], [("a", 100)])[0][1] == pytest.approx(2 / 61)
    assert [name for name, _ in rrf_merge([("b", 1)], [("a", 2)])] == ["a", "b"]


def test_config_is_opt_in_and_needs_no_openai_key():
    assert not LegionEmbeddingSettings.from_env({}).enabled
    configured = LegionEmbeddingSettings.from_env({"GEMINI_API_KEY": "fake"}, enabled=True)
    assert configured.model == "gemini-embedding-2"
    assert configured.dimensions == 3072
    assert not LegionEmbeddingSettings.from_env({"SAGE_LEGION_EMBEDDINGS_ENABLED": "true"}, enabled=False).enabled
    with pytest.raises(ConfigurationError):
        LegionEmbeddingSettings.from_env({"SAGE_LEGION_QDRANT_PATH": "/tmp/a", "SAGE_LEGION_QDRANT_URL": "http://localhost:6333"}, enabled=True)
    with pytest.raises(ConfigurationError):
        LegionEmbeddingSettings.from_env({"SAGE_GOOGLE_MODEL_CONTEXT_APPROVED": "false"}, enabled=True)


def test_gemini_uses_one_document_no_task_type_and_closes_client(monkeypatch):
    from google import genai

    calls = []
    closed = []

    class Client:
        def __init__(self, **kwargs):
            self.models = self
        def __enter__(self):
            return self
        def __exit__(self, *args):
            closed.append(True)
        def embed_content(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(embeddings=[SimpleNamespace(values=[1., 0., 0.])])

    monkeypatch.setattr(genai, "Client", Client)
    provider = GeminiEmbeddingProvider(api_key="fake", dimensions=3)
    assert provider.embed("title: helper | text: refund", query=False, timeout=1) == [1., 0., 0.]
    assert calls[0]["model"] == "gemini-embedding-2"
    assert isinstance(calls[0]["contents"], str)
    assert calls[0]["config"].task_type is None
    assert closed == [True]
    with pytest.raises(MemoryVectorError):
        provider._values(SimpleNamespace(embeddings=[]))
    for vector in ([0., 0., 0.], [float("nan"), 1., 1.], [1.]):
        with pytest.raises(MemoryVectorError):
            validate_vector(vector, 3)


def test_missing_key_is_safe_and_auth_validation_is_not_retried(monkeypatch):
    from google import genai
    from google.genai import errors

    with pytest.raises(MemoryVectorError, match="GEMINI_API_KEY"):
        GeminiEmbeddingProvider(api_key=None, dimensions=3).embed("code", query=False, timeout=1)
    calls = []

    class Client:
        def __init__(self, **kwargs):
            self.models = self
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def embed_content(self, **kwargs):
            calls.append(kwargs)
            raise errors.APIError(403, {"error": {"message": "DO-NOT-LOG-SECRET"}})

    monkeypatch.setattr(genai, "Client", Client)
    provider = GeminiEmbeddingProvider(api_key="fake", dimensions=3)
    with pytest.raises(MemoryVectorError) as error:
        provider.embed("code", query=False, timeout=1)
    assert "DO-NOT-LOG-SECRET" not in str(error.value)
    assert len(calls) == 1
    assert provider.usage.retries == 0


def test_query_vector_usage_is_recorded_in_native_tool_session(fixture_repo, tmp_path):
    import asyncio
    from sage.agents.memory_tools import build_legion_memory_tools
    from sage.agents.prompts import build_solver_message
    from sage.legion_memory.session import MemorySession

    service, provider = vector_service(tmp_path)
    database = tmp_path / "graph.sqlite3"
    build = service.build_or_update_graph_tool(repo_root=fixture_repo, memory_file=database)
    retrieval = service.retrieve_issue_context(issue_text="reimburse", repo_root=fixture_repo, memory_file=database)
    session = MemorySession(service, fixture_repo, database, database, build, retrieval)
    message = build_solver_message(base_sha=build.indexed_sha, issue_text="reimburse", memory_context=session.initial_context)
    assert "service.py::helper" in message
    tools = {t.name: t for t in build_legion_memory_tools(service, repo_root=fixture_repo,
        memory_file=database, usage_recorder=session.record_tool_call)}
    asyncio.run(tools["semantic_search_nodes_tool"].ainvoke({"query": "reimburse", "limit": 3}))
    artifact = session.artifact()
    assert artifact.embedding_usage.query_calls == 1  # Initial query is reused.
    assert len(artifact.tool_calls) == 1
    assert artifact.embedding_usage.document_calls == build.vectors.embedded
    session.close()
    assert not service.vectors._queries


def test_cli_embedding_build_failure_is_strict_without_chat_credentials(fixture_repo, tmp_path, monkeypatch, capsys):
    from sage.cli import main

    for key in ("OPENAI_API_KEY", "GEMINI_API_KEY", "SAGE_LEGION_QDRANT_URL", "SAGE_LEGION_QDRANT_PATH"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("SAGE_GOOGLE_MODEL_CONTEXT_APPROVED", "true")
    status = main(["memory", "build", "--repo", str(fixture_repo), "--memory-file", str(tmp_path / "graph.sqlite3"), "--embeddings", "on"])
    output = capsys.readouterr().out
    assert status == 1
    assert "Legion Memory build: ready" in output
    assert "Embeddings: unavailable" in output
    assert "GEMINI_API_KEY is missing" in output
