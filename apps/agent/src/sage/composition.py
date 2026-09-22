"""Production dependency construction for the single Sage solve architecture."""

from pathlib import Path

from langchain_openai import ChatOpenAI

from sage.agents.reviewer import ReviewerAgent
from sage.agents.solver import SolverAgent
from sage.config import Settings, LegionEmbeddingSettings
from sage.integrations.qdrant import QdrantVectorStore
from sage.legion_memory.vectors import VectorIndex
from sage.providers.embeddings import GeminiEmbeddingProvider
from sage.errors import ConfigurationError
from sage.legion_memory.service import LegionMemoryService
from sage.orchestration.solve import SolveOrchestrator
from sage.providers.google import GoogleProvider
from sage.providers.typesafe import TypeSafeProvider
from sage.orchestration.navigation import NavigationSession


def build_legion_memory_service(*, data_root: Path | None = None,
                               embeddings: LegionEmbeddingSettings | None = None) -> LegionMemoryService:
    """Construct optional adapters only for explicitly memory-enabled callers."""

    if embeddings is None or not embeddings.enabled:
        return LegionMemoryService(data_root=data_root)
    provider = GeminiEmbeddingProvider(api_key=embeddings.api_key,
        dimensions=embeddings.dimensions, retries=embeddings.retries, concurrency=embeddings.concurrency)

    def vector_store(database: Path, collection: str, create: bool) -> QdrantVectorStore:
        local_path = None
        if embeddings.qdrant_url is None:
            local_path = embeddings.qdrant_path or database.parent / "qdrant"
        return QdrantVectorStore(collection=collection, dimensions=embeddings.dimensions,
            usage=provider.usage, path=local_path, url=embeddings.qdrant_url,
            api_key=embeddings.qdrant_api_key, create=create)

    return LegionMemoryService(data_root=data_root, vectors=VectorIndex(provider, vector_store,
        max_nodes=embeddings.max_nodes, deadline=embeddings.deadline_seconds,
        request_timeout=embeddings.request_timeout_seconds, min_similarity=embeddings.min_similarity))


def build_orchestrator(settings: Settings) -> SolveOrchestrator:
    """Construct the concrete Solver, Reviewer, and deterministic coordinator."""

    if not settings.google_model_context_approved:
        raise ConfigurationError("Google model context use is not acknowledged.")
    if not settings.gemini_api_key or not settings.openai_api_key:
        raise ConfigurationError("Solver and Reviewer credentials are incomplete.")

    solver_model = ChatOpenAI(
        model=settings.solver_model,
        api_key=settings.openai_api_key,
        max_retries=settings.openai_max_retries,
        timeout=float(settings.model_request_timeout_seconds),
        use_responses_api=True,
    )
    reviewer_provider = GoogleProvider(
        api_key=settings.gemini_api_key,
        model_name=settings.reviewer_model,
        timeout_seconds=settings.model_request_timeout_seconds,
    )
    def navigation_factory(**arguments) -> NavigationSession:
        assert settings.jev.api_key is not None
        provider = TypeSafeProvider(api_key=settings.jev.api_key, model=settings.jev.model,
            capture=settings.jev.capture, log_input=settings.jev.mode == "on" and settings.jev.log_input,
            run_id=arguments["context"].prepared_run.run_id)
        return NavigationSession(provider=provider, **arguments)

    return SolveOrchestrator(
        solver=SolverAgent(settings=settings, model=solver_model),
        reviewer=ReviewerAgent(settings=settings),
        reviewer_provider=reviewer_provider,
        navigation_factory=navigation_factory if settings.jev.mode != "off" else None,
    )
