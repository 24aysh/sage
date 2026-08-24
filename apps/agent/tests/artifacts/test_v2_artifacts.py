from pathlib import Path

import pytest

from sage.artifacts.v2 import V2ArtifactStore
from sage.domain.solver import (
    SavedSolverPlan,
    SolverAcceptanceCriterion,
    SolverPlan,
    SolverPlanTask,
)
from sage.domain.usage import RunProvenance
from sage.errors import ArtifactError


def test_v2_artifact_store_writes_fixed_atomic_artifacts(tmp_path: Path) -> None:
    store = V2ArtifactStore(tmp_path)

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


def test_v2_artifact_stage_names_cannot_escape_run_directory(tmp_path: Path) -> None:
    store = V2ArtifactStore(tmp_path)

    with pytest.raises(ArtifactError, match="stage name"):
        store.write_verification_log(1, "../../outside", "no")

    assert list(tmp_path.iterdir()) == []
