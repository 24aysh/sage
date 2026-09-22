import pytest
from types import SimpleNamespace

from sage.agents.reviewer import ReviewerAgent
from sage.agents.solver import SolverAgent
from sage.composition import build_legion_memory_service, build_orchestrator
from sage.config import JevSettings, LegionEmbeddingSettings, Settings
from sage.errors import ConfigurationError
from sage.legion_memory.service import LegionMemoryService
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
    assert orchestrator._navigation_factory is None
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


def test_composition_builds_legion_memory_without_provider_credentials(tmp_path) -> None:
    service = build_legion_memory_service(data_root=tmp_path)

    assert isinstance(service, LegionMemoryService)
    assert service._data_root == tmp_path


def test_composition_keeps_github_lexical_memory_provider_free(tmp_path) -> None:
    service = build_legion_memory_service(
        data_root=tmp_path,
        embeddings=LegionEmbeddingSettings.from_github_env(
            {"SAGE_LEGION_EMBEDDINGS_ENABLED": "false"}
        ),
    )

    assert service.vectors is None


@pytest.mark.parametrize("mode,log_input,expected", [
    ("off", True, None), ("shadow", True, False), ("on", True, True), ("on", False, False),
])
def test_composition_only_logs_inputs_when_on_and_allowed(monkeypatch, settings, mode, log_input, expected):
    providers = []
    monkeypatch.setattr("sage.composition.TypeSafeProvider", lambda **kw: providers.append(kw))
    monkeypatch.setattr("sage.composition.NavigationSession", lambda **kw: kw)
    config = settings.model_copy(update={"gemini_api_key": "gemini-test",
        "jev": JevSettings(mode=mode, api_key="test", log_input=log_input)})
    orchestrator = build_orchestrator(config)
    if mode == "off":
        assert orchestrator._navigation_factory is None
        assert not providers
    else:
        context = SimpleNamespace(prepared_run=SimpleNamespace(run_id="run-1"))
        orchestrator._navigation_factory(context=context)
        assert providers[0]["log_input"] is expected
        assert providers[0]["run_id"] == "run-1"
