"""Deterministic semantic guards for V2 model results."""

from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath

from sage.domain.admission import (
    AutonomyContract,
    DimensionStatus,
    IntakeResult,
    ReadinessDisposition,
)
from sage.domain.planning import ExecutionPlan, RetrievalRequest
from sage.domain.review import ReviewFailureType, ReviewResult, ReviewVerdict
from sage.errors import AgentRuntimeError, RepositoryError
from sage.repository.patch import normalize_null_file_headers
from sage.repository.scope import validate_write_scopes

_READY_REQUIRED = (
    "objective_clarity",
    "expected_behavior_clarity",
    "acceptance_testability",
    "scope_boundedness",
    "repository_evidence_sufficiency",
    "design_choice_closed",
)
_READY_OPTIONAL_NA = (
    "external_dependency_availability",
    "sandbox_compatibility",
    "permission_or_credential_independence",
    "cross_repository_dependency",
    "human_approval_dependency",
)


class InvalidModelContractError(AgentRuntimeError):
    """A role result passed schema parsing but violated V2 semantics."""


def validate_ready_intake(intake: IntakeResult) -> ExecutionPlan:
    """Validate autonomy dimensions and plan before mutation is admitted."""

    if intake.disposition is not ReadinessDisposition.READY_AUTONOMOUS:
        raise InvalidModelContractError("Only ready intake can cross autonomy admission.")
    if intake.plan is None:
        raise InvalidModelContractError("Ready intake is missing its plan.")
    dimensions = intake.dimensions
    for name in _READY_REQUIRED:
        if getattr(dimensions, name).status is not DimensionStatus.SUFFICIENT:
            raise InvalidModelContractError(
                f"Ready intake has a blocking dimension: {name}."
            )
    for name in _READY_OPTIONAL_NA:
        if getattr(dimensions, name).status not in {
            DimensionStatus.SUFFICIENT,
            DimensionStatus.NOT_APPLICABLE,
        }:
            raise InvalidModelContractError(
                f"Ready intake has a blocking dimension: {name}."
            )
    validate_plan(intake.plan)
    return intake.plan


def validate_plan(plan: ExecutionPlan) -> None:
    """Validate path, scope, retrieval, and privilege boundaries."""

    validate_write_scopes(plan.allowed_write_scopes)
    for task in plan.tasks:
        for path in task.relevant_paths:
            _validate_relative_value(path, label="plan path")
    for request in (*plan.retrieval_requests,):
        validate_retrieval_request(request)
    forbidden_commands = ("git push", "git commit", "gh ", "curl ", "wget ")
    for hint in plan.verification_hints:
        if any(value in hint.command for value in forbidden_commands):
            raise InvalidModelContractError(
                "Plan requested a privileged verification command."
            )


def validate_retrieval_request(request: RetrievalRequest) -> None:
    if request.path is not None:
        _validate_relative_value(request.path, label="retrieval path")
    if not request.value.strip() or "\x00" in request.value:
        raise InvalidModelContractError("Retrieval request value is invalid.")


def build_autonomy_contract(
    *,
    plan: ExecutionPlan,
    base_sha: str,
    profile: str,
    model_calls_remaining: int = 5,
    implementation_repairs_remaining: int = 1,
    review_repairs_remaining: int = 1,
    solver_context_expansions_remaining: int = 1,
) -> AutonomyContract:
    """Freeze a digest-addressed mutation contract from a validated plan."""

    validate_plan(plan)
    canonical = json.dumps(
        plan.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )
    return AutonomyContract(
        task_summary=plan.task_summary,
        acceptance_contract=tuple(
            criterion.model_dump(mode="json") for criterion in plan.acceptance_contract
        ),
        safe_assumptions=plan.safe_assumptions,
        allowed_write_scopes=validate_write_scopes(plan.allowed_write_scopes),
        non_blocking_uncertainties=plan.non_blocking_uncertainties,
        available_capabilities=(
            "read bounded repository context",
            "apply one unified patch",
            "run bounded sandbox verification",
        ),
        forbidden_capabilities=(
            "network access",
            "provider or GitHub credentials in sandbox",
            "parallel workers",
            "publication or merge",
        ),
        verification_expectations=tuple(
            criterion.verification for criterion in plan.acceptance_contract
        ),
        provider_profile=profile,
        model_calls_remaining=model_calls_remaining,
        implementation_repairs_remaining=implementation_repairs_remaining,
        review_repairs_remaining=review_repairs_remaining,
        solver_context_expansions_remaining=solver_context_expansions_remaining,
        base_sha=base_sha,
        plan_digest=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )


def validate_review(review: ReviewResult, *, plan: ExecutionPlan) -> None:
    """Reject goalpost movement and incomplete criterion review."""

    expected = {item.criterion_id for item in plan.acceptance_contract}
    actual = {item.criterion_id for item in review.criterion_results}
    if review.verdict is ReviewVerdict.PASS and actual != expected:
        raise InvalidModelContractError(
            "Passing review must cover every acceptance criterion."
        )
    for finding in (*review.blocking_findings, *review.optional_findings):
        if not set(finding.criterion_ids).issubset(expected):
            raise InvalidModelContractError(
                "Review finding references an unknown criterion."
            )
    if (
        review.verdict is ReviewVerdict.FAIL
        and review.failure_type is not ReviewFailureType.IMPLEMENTATION
        and review.blocking_findings
    ):
        # Non-implementation failures are terminal and cannot enter repair.
        return


def normalize_patch(value: str) -> str:
    """Apply one safe syntax normalization without an open-ended repair loop."""

    patch = value.strip().replace("\r\n", "\n").replace("\r", "\n")
    if patch.startswith("```diff\n") and patch.endswith("```"):
        patch = patch[len("```diff\n") : -3].rstrip()
    elif patch.startswith("```patch\n") and patch.endswith("```"):
        patch = patch[len("```patch\n") : -3].rstrip()
    patch = normalize_null_file_headers(patch)
    if "GIT binary patch" in patch or "Binary files " in patch:
        raise RepositoryError("Binary patches are unsupported in the V2 prototype.")
    if patch.startswith(("*** Begin Patch", "*** Update File")):
        raise RepositoryError(
            "Solver returned apply-patch markers; return a unified Git diff "
            "beginning with 'diff --git a/' or '--- a/'."
        )
    if not patch.startswith(("diff --git ", "--- ")):
        raise RepositoryError(
            "Solver patch is not a unified Git diff beginning with "
            "'diff --git a/' or '--- a/'."
        )
    return f"{patch}\n"


def _validate_relative_value(value: str, *, label: str) -> None:
    pure = PurePosixPath(value)
    if pure.is_absolute() or ".." in pure.parts or ".git" in pure.parts:
        raise InvalidModelContractError(f"Unsafe {label}.")
