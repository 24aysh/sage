from pathlib import Path

from sage.config import Settings
from sage.domain.review import ReviewFailureType, ReviewFinding, ReviewResult, ReviewVerdict
from sage.domain.solver import (
    SavedSolverPlan,
    SolverAcceptanceCriterion,
    SolverFinalResult,
    SolverOutcome,
    SolverPlan,
    SolverPlanTask,
)
from sage.runtimes.v2.graph import create_candidate_snapshot, review_fingerprint


class Repository:
    def get_head_sha(self) -> str:
        return "a" * 40

    def get_complete_diff(self) -> str:
        return "diff --git a/app.py b/app.py\n+value = 2\n"

    def get_changed_files(self) -> list[str]:
        return ["app.py"]


def test_candidate_snapshot_uses_git_not_solver_file_claims() -> None:
    plan = _saved_plan()
    result = SolverFinalResult(
        outcome=SolverOutcome.IMPLEMENTED,
        summary="Updated the value.",
        plan_version=1,
    )

    snapshot = create_candidate_snapshot(
        repository=Repository(),  # type: ignore[arg-type]
        base_sha="a" * 40,
        plan=plan,
        solver_result=result,
        max_diff_chars=10_000,
    )

    assert snapshot.changed_files == ("app.py",)
    assert snapshot.diff.startswith("diff --git")
    assert snapshot.plan_digest == plan.digest


def test_review_fingerprint_is_stable_for_equivalent_findings() -> None:
    first = _review("Value remains wrong")
    second = _review("  value   remains WRONG ")

    assert review_fingerprint(first) == review_fingerprint(second)


def _saved_plan() -> SavedSolverPlan:
    plan = SolverPlan(
        issue_summary="Change the value.",
        approach="Edit app.py.",
        tasks=(
            SolverPlanTask(
                task_id="edit",
                objective="Update app.py.",
                criterion_ids=("value",),
            ),
        ),
        acceptance_criteria=(
            SolverAcceptanceCriterion(
                criterion_id="value",
                requirement="Value equals two.",
            ),
        ),
        status="implementable",
    )
    return SavedSolverPlan(version=1, digest=plan.digest(), plan=plan)


def _review(evidence: str) -> ReviewResult:
    return ReviewResult(
        verdict=ReviewVerdict.FAIL,
        failure_type=ReviewFailureType.IMPLEMENTATION,
        blocking_findings=(
            ReviewFinding(
                finding_id="wrong-value",
                criterion_ids=("value",),
                evidence=evidence,
                required_outcome="Set the value to two.",
                path="app.py",
                line=1,
            ),
        ),
        confidence=0.9,
    )
