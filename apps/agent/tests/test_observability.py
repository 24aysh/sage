from sage.domain.usage import AttemptKind, ModelRole
from sage.observability import agent_trace_config, workflow_trace_config


def test_workflow_trace_has_stable_name_and_safe_correlation_metadata() -> None:
    config = workflow_trace_config(
        run_id="run-123",
        graph_name="v2",
        model_profile="constrained-cross-provider",
    )

    assert config["run_name"] == "Sage V2 Workflow"
    assert config["tags"] == [
        "sage-v2",
        "profile:constrained-cross-provider",
    ]
    assert config["metadata"] == {
        "sage_run_id": "run-123",
        "sage_graph": "v2",
        "sage_runtime": "v2",
        "sage_model_profile": "constrained-cross-provider",
    }
    assert config["recursion_limit"] == 80


def test_agent_trace_omits_missing_local_run_id() -> None:
    config = agent_trace_config(
        run_id=None,
        role=ModelRole.REVIEWER,
        stage="review",
        attempt=AttemptKind.PRIMARY,
        provider="google",
        model="gemini-3.5-flash",
        call_number=3,
    )

    assert config["run_name"] == "Reviewer"
    assert "sage_run_id" not in config["metadata"]


def test_admission_trace_is_named_without_creating_a_new_model_configuration() -> None:
    config = agent_trace_config(
        run_id="run-123",
        role=ModelRole.ADMISSION,
        stage="admission",
        attempt=AttemptKind.PRIMARY,
        provider="openai",
        model="solver-model",
        call_number=1,
    )

    assert config["run_name"] == "Admission"
    assert "role:admission" in config["tags"]
    assert config["metadata"]["sage_model"] == "solver-model"
