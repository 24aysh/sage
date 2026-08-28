import pytest
from pydantic import ValidationError

from sage.domain.review import CriterionResult, ReviewResult, ReviewVerdict
from sage.domain.solver import (
    SavedSolverPlan,
    SolverAcceptanceCriterion,
    SolverFinalResult,
    SolverOutcome,
    SolverPlan,
    SolverPlanTask,
)
from sage.runtimes.v2.validation import (
    InvalidModelContractError,
    validate_review,
    validate_solver_final,
)


def test_solver_must_reference_latest_saved_plan() -> None:
    with pytest.raises(InvalidModelContractError, match="latest"):
        validate_solver_final(
            SolverFinalResult(
                outcome=SolverOutcome.IMPLEMENTED,
                summary="done",
                plan_version=2,
            ),
            plan=_plan(),
        )


def test_reviewer_pass_requires_complete_criterion_coverage() -> None:
    with pytest.raises(InvalidModelContractError, match="complete"):
        validate_review(
            ReviewResult(verdict=ReviewVerdict.PASS, confidence=0.9),
            plan=_plan(),
        )

    validate_review(
        ReviewResult(
            verdict=ReviewVerdict.PASS,
            criterion_results=(
                CriterionResult(
                    criterion_id="value",
                    satisfied=True,
                    evidence="Diff sets value to two.",
                ),
            ),
            confidence=0.9,
        ),
        plan=_plan(),
    )


def test_solver_plan_rejects_removed_context_fields() -> None:
    payload = _plan().plan.model_dump(mode="json")
    payload["admission_context_digest"] = "a" * 64
    payload["admission_evidence_ids"] = ["old-evidence"]

    with pytest.raises(ValidationError, match="extra_forbidden"):
        SolverPlan.model_validate(payload)


def _plan() -> SavedSolverPlan:
    plan = SolverPlan(
        issue_summary="Change value.",
        approach="Edit the file.",
        tasks=(
            SolverPlanTask(
                task_id="edit",
                objective="Edit.",
                criterion_ids=("value",),
            ),
        ),
        acceptance_criteria=(
            SolverAcceptanceCriterion(
                criterion_id="value",
                requirement="Value is two.",
            ),
        ),
        status="implementable",
    )
    return SavedSolverPlan(version=1, digest=plan.digest(), plan=plan)
