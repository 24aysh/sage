"""Production dependency construction for the single Sage solve architecture."""

from pathlib import Path

from langchain_openai import ChatOpenAI

from sage.agents.reviewer import ReviewerAgent
from sage.agents.solver import SolverAgent
from sage.config import Settings
from sage.errors import ConfigurationError
from sage.harness.memory.service import LegionMemoryService
from sage.orchestration.solve import SolveOrchestrator
from sage.providers.google import GoogleProvider
from sage.harness.jev.provider import TypeSafeProvider
from sage.harness.jev.session import NavigationSession


def build_legion_memory_service(*, data_root: Path | None = None) -> LegionMemoryService:
    """Construct the local, deterministic graph capability; no model credentials."""
    return LegionMemoryService(data_root=data_root)


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
