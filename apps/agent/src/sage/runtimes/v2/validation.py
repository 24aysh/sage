"""Deterministic validation for Solver plans, candidates, and reviews."""

from __future__ import annotations

from sage.domain.review import ReviewResult, ReviewVerdict
from sage.domain.solver import SavedSolverPlan, SolverFinalResult


class InvalidModelContractError(ValueError):
    """Raised when a role violates a cross-field V2 contract."""


def validate_solver_final(
    result: SolverFinalResult,
    *,
    plan: SavedSolverPlan | None,
) -> None:
    if plan is None:
        raise InvalidModelContractError("Solver finished without saving a plan.")
    if result.plan_version != plan.version:
        raise InvalidModelContractError(
            "Solver final result does not reference the latest saved plan."
        )
    if result.outcome.value == "implemented" and plan.plan.status != "implementable":
        raise InvalidModelContractError(
            "Solver claimed implementation under a blocked plan."
        )


def validate_review(review: ReviewResult, *, plan: SavedSolverPlan) -> None:
    criterion_ids = {
        criterion.criterion_id for criterion in plan.plan.acceptance_criteria
    }
    result_ids = [item.criterion_id for item in review.criterion_results]
    if len(result_ids) != len(set(result_ids)):
        raise InvalidModelContractError("Reviewer returned duplicate criterion results.")
    unknown = set(result_ids) - criterion_ids
    if unknown:
        raise InvalidModelContractError(
            "Reviewer referenced unknown criteria: " + ", ".join(sorted(unknown))
        )
    if review.verdict is ReviewVerdict.PASS:
        missing = criterion_ids - set(result_ids)
        unsatisfied = {
            item.criterion_id for item in review.criterion_results if not item.satisfied
        }
        if missing or unsatisfied:
            raise InvalidModelContractError(
                "Reviewer pass requires complete satisfied criterion coverage."
            )
