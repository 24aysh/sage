"""Production dependency construction for the single Sage solve architecture."""

from langchain_openai import ChatOpenAI

from sage.agents.reviewer import ReviewerAgent
from sage.agents.solver import SolverAgent
from sage.config import Settings
from sage.errors import ConfigurationError
from sage.orchestration.solve import SolveOrchestrator
from sage.providers.google import GoogleProvider
from sage.research.service import build_research_service


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
        research_service=build_research_service(settings),
    )
