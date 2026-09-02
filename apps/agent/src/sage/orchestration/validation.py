"""Deterministic validation for Solver cross-field contracts."""

from __future__ import annotations

from langgraph.errors import GraphRecursionError
from openai import APIStatusError, AuthenticationError, RateLimitError
from pydantic import ValidationError

from sage.domain.review import ReviewFailureType, ReviewResult
from sage.domain.solve import AgentFinalOutput, SolveOutcome
from sage.domain.solver import SavedSolverPlan, SolverFinalResult, SolverOutcome
from sage.domain.verification import VerificationResult, VerificationStatus
from sage.errors import AgentRuntimeError, InvalidModelContractError
from sage.providers.calls import FinalizationReserveError, ModelCalls
from sage.providers.errors import ProviderErrorCategory, ProviderInvocationError
from sage.providers.openai import is_openai_quota_error


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


def solver_terminal(
    result: SolverFinalResult,
    *,
    calls: ModelCalls,
) -> AgentFinalOutput | None:
    """Map non-implementation Solver outcomes to terminal solve outcomes."""

    outcome = {
        SolverOutcome.NO_CHANGE: SolveOutcome.NO_CHANGE,
        SolverOutcome.BLOCKED: SolveOutcome.HUMAN_REQUIRED_AFTER_START,
        SolverOutcome.UNRESOLVED: SolveOutcome.UNRESOLVED,
    }.get(result.outcome)
    if outcome is None:
        return None
    return AgentFinalOutput(
        summary=result.summary,
        outcome=outcome,
        remaining_uncertainty=list(result.remaining_uncertainty),
        provenance=calls.provenance(),
    )


def review_failure_terminal(
    review: ReviewResult,
    calls: ModelCalls,
) -> AgentFinalOutput:
    """Map non-repairable review failures to a safe terminal outcome."""

    if review.failure_type is ReviewFailureType.ENVIRONMENT:
        outcome = SolveOutcome.ENVIRONMENT_BLOCKED
    elif review.failure_type is ReviewFailureType.REQUIREMENT_AMBIGUITY:
        outcome = SolveOutcome.HUMAN_REQUIRED_AFTER_START
    else:
        outcome = SolveOutcome.REVIEW_FAILED
    return terminal(outcome, "Independent review rejected the candidate.", calls)


def provider_terminal(
    error: ProviderInvocationError,
    calls: ModelCalls,
) -> AgentFinalOutput:
    """Map normalized provider errors to stable solve outcomes."""

    if error.category is ProviderErrorCategory.RATE_LIMITED:
        outcome = SolveOutcome.RATE_LIMITED
    elif error.category is ProviderErrorCategory.SCHEMA_ERROR:
        outcome = SolveOutcome.INVALID_MODEL_OUTPUT
    else:
        outcome = SolveOutcome.PROVIDER_UNAVAILABLE
    return terminal(
        outcome,
        f"The configured {error.provider} provider was unavailable for its role.",
        calls,
    )


def failure_terminal(
    error: Exception,
    calls: ModelCalls,
) -> AgentFinalOutput | None:
    """Map expected execution failures while leaving unknown failures visible."""

    if isinstance(error, ProviderInvocationError):
        return provider_terminal(error, calls)
    if isinstance(error, AuthenticationError):
        return terminal(
            SolveOutcome.PROVIDER_UNAVAILABLE,
            "OpenAI rejected the configured coding API key or authorization.",
            calls,
        )
    if isinstance(error, RateLimitError):
        outcome = (
            SolveOutcome.PROVIDER_UNAVAILABLE
            if is_openai_quota_error(error)
            else SolveOutcome.RATE_LIMITED
        )
        return terminal(outcome, "The coding provider could not serve the run.", calls)
    if isinstance(error, APIStatusError):
        return terminal(
            SolveOutcome.PROVIDER_UNAVAILABLE,
            "The coding provider rejected a required request.",
            calls,
        )
    if isinstance(error, FinalizationReserveError):
        return terminal(
            SolveOutcome.BUDGET_EXHAUSTED,
            "Sage reached the run deadline finalization reserve.",
            calls,
        )
    if isinstance(error, (ValidationError, InvalidModelContractError)):
        return terminal(
            SolveOutcome.INVALID_MODEL_OUTPUT,
            "A model role returned an invalid structured result.",
            calls,
        )
    if isinstance(error, GraphRecursionError):
        return terminal(
            SolveOutcome.UNRESOLVED,
            "A coding node exhausted its configured tool-loop turn limit.",
            calls,
        )
    if isinstance(error, AgentRuntimeError):
        return terminal(
            SolveOutcome.UNRESOLVED,
            "Sage could not produce and review a stable authoritative candidate.",
            calls,
        )
    return None


def terminal(
    outcome: SolveOutcome,
    summary: str,
    calls: ModelCalls,
) -> AgentFinalOutput:
    """Create one terminal output with current model-call provenance."""

    return AgentFinalOutput(summary=summary, outcome=outcome, provenance=calls.provenance())


def verification_fingerprint(result: VerificationResult) -> str:
    """Return the stable failure identity used to stop stalled repairs."""

    return "|".join(
        check.fingerprint
        for check in result.checks
        if check.status in {VerificationStatus.FAIL, VerificationStatus.TIMEOUT}
    )
