from pathlib import Path

import pytest

from sage.artifacts.store import RunArtifacts
from sage.domain.retrieval import RetrievalRunArtifact, RetrievalStatus
from sage.domain.solver import (
    SavedSolverPlan,
    SolverAcceptanceCriterion,
    SolverPlan,
    SolverPlanTask,
)
from sage.domain.usage import RunProvenance
from sage.errors import ArtifactError


def test_run_artifacts_writes_fixed_atomic_stage_artifacts(tmp_path: Path) -> None:
    store = RunArtifacts(tmp_path)

    usage_path = store.write_usage(RunProvenance())
    plan = SolverPlan(
        issue_summary="Change a value.",
        approach="Edit and test.",
        tasks=(SolverPlanTask(task_id="edit", objective="Edit the value."),),
        acceptance_criteria=(
            SolverAcceptanceCriterion(
                criterion_id="value",
                requirement="The value is updated.",
            ),
        ),
        status="implementable",
    )
    saved = SavedSolverPlan(version=1, digest=plan.digest(), plan=plan)
    plan_path = store.write_solver_plan(1, saved)

    assert usage_path == tmp_path / "usage.json"
    assert plan_path == tmp_path / "solver-plans/01.json"
    assert (tmp_path / "solver-plan.json").is_file()
    assert "constrained-cross-provider" in usage_path.read_text()


def test_run_artifacts_writes_repository_retrieval_evidence(tmp_path: Path) -> None:
    store = RunArtifacts(tmp_path)
    index_file = tmp_path / "graph.sqlite3"

    path = store.write_retrieval(
        RetrievalRunArtifact(
            requested_index_file=index_file,
            resolved_index_file=index_file,
            status=RetrievalStatus.UNAVAILABLE,
            failure_category="RetrievalBuildError",
            fallback="normal repository inspection",
        )
    )

    assert path == tmp_path / "repository-retrieval.json"
    assert '"status": "unavailable"' in path.read_text(encoding="utf-8")


def test_artifact_stage_names_cannot_escape_run_directory(tmp_path: Path) -> None:
    store = RunArtifacts(tmp_path)

    with pytest.raises(ArtifactError, match="stage name"):
        store.write_verification_log(1, "../../outside", "no")

    assert list(tmp_path.iterdir()) == []
