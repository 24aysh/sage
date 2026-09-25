"""Production dependency construction for the single Sage solve architecture."""

from pathlib import Path

from langchain_openai import ChatOpenAI

from sage.agents.reviewer import ReviewerAgent
from sage.agents.solver import SolverAgent
from sage.config import JevSettings, Settings
from sage.errors import ConfigurationError
from sage.harness.retrieval.service import RepositoryRetrievalService
from sage.orchestration.solve import SolveOrchestrator
from sage.providers.google import GoogleProvider
from sage.harness.jev.provider import TypeSafeProvider
from sage.harness.jev.filter import RelevanceFilter


def build_retrieval_service(*, data_root: Path | None = None) -> RepositoryRetrievalService:
    """Construct the local, deterministic graph capability; no model credentials."""
    return RepositoryRetrievalService(data_root=data_root)


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
    return SolveOrchestrator(
        solver=SolverAgent(settings=settings, model=solver_model),
        reviewer=ReviewerAgent(settings=settings),
        reviewer_provider=reviewer_provider,
        relevance_filter_factory=(lambda run_id: build_relevance_filter(settings.jev, run_id=run_id))
            if settings.jev.mode != "off" else None,
    )


def build_relevance_filter(settings: JevSettings, *, run_id: str | None = None) -> RelevanceFilter:
    """Standalone retrieval needs only Jev credentials, never Solver/Reviewer keys."""
    provider = None
    if settings.mode != "off":
        assert settings.api_key is not None
        provider = TypeSafeProvider(api_key=settings.api_key, model=settings.model,
            capture=settings.capture, log_input=settings.mode == "on" and settings.log_input,
            run_id=run_id)
    return RelevanceFilter(settings=settings, provider=provider)
