import pytest

from sage.agents.reviewer import ReviewerAgent
from sage.agents.solver import SolverAgent
from sage.composition import build_orchestrator
from sage.config import Settings
from sage.errors import ConfigurationError
from sage.orchestration.solve import SolveOrchestrator


def test_composition_builds_the_single_solve_architecture() -> None:
    orchestrator = build_orchestrator(
        Settings(
            openai_api_key="openai-test",
            gemini_api_key="gemini-test",
            solver_model="solver-model",
            reviewer_model="reviewer-model",
        )
    )

    assert isinstance(orchestrator, SolveOrchestrator)
    assert isinstance(orchestrator._solver, SolverAgent)
    assert isinstance(orchestrator._reviewer, ReviewerAgent)
    assert (
        orchestrator._reviewer_provider.provider_name,
        orchestrator._reviewer_provider.model_name,
    ) == ("google", "reviewer-model")


def test_composition_refuses_rejected_google_context_use() -> None:
    with pytest.raises(ConfigurationError, match="not acknowledged"):
        build_orchestrator(
            Settings(
                openai_api_key="openai-test",
                gemini_api_key="gemini-test",
                google_model_context_approved=False,
            )
        )
