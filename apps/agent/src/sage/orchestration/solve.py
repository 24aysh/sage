"""Deterministic solve, verify, review, repair, and terminal coordination."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from langchain_core.runnables import RunnableLambda

from sage.agents.reviewer import ReviewerAgent
from sage.agents.solver import (
    SOLVE_GRAPH_NAME,
    SolverAgent,
    SolverPlanSession,
    build_repair_message,
    build_solver_message,
)
from sage.domain.review import ReviewFailureType, ReviewVerdict
from sage.domain.solve import AgentFinalOutput, SolveOutcome
from sage.domain.verification import VerificationStatus
from sage.errors import AgentRuntimeError, InvalidModelContractError
from sage.observability import workflow_trace_config
from sage.orchestration.candidate import (
    create_candidate_snapshot,
    ensure_candidate_unchanged,
    review_fingerprint,
)
from sage.orchestration.context import SolveContext
from sage.orchestration.validation import (
    failure_terminal,
    review_failure_terminal,
    solver_terminal,
    terminal,
    validate_solver_final,
    verification_fingerprint,
)
from sage.providers.calls import ModelCalls
from sage.research.service import ResearchService
from sage.verification.runner import Verifier

if TYPE_CHECKING:
    from sage.providers.base import ModelProvider

logger = logging.getLogger(__name__)
_MODEL_PROFILE_LABEL = "constrained-cross-provider"


class SolveOrchestrator:
    """Solve through tools, verify deterministically, and review independently."""

    def __init__(
        self,
        *,
        solver: SolverAgent,
        reviewer: ReviewerAgent,
        reviewer_provider: ModelProvider,
        research_service: ResearchService,
    ) -> None:
        self._solver = solver
        self._reviewer = reviewer
        self._reviewer_provider = reviewer_provider
        self._research_service = research_service

    async def solve(
        self,
        *,
        issue_text: str,
        context: SolveContext,
    ) -> AgentFinalOutput:
        """Run one solve under a parent trace with nested role activity."""

        async def workflow(_: dict[str, object]) -> AgentFinalOutput:
            return await self._solve(issue_text=issue_text, context=context)

        return await RunnableLambda(workflow).ainvoke(
            {},
            config=workflow_trace_config(
                run_id=context.prepared_run.run_id,
                graph_name=SOLVE_GRAPH_NAME,
                model_profile=_MODEL_PROFILE_LABEL,
            ),
        )

    async def _solve(
        self,
        *,
        issue_text: str,
        context: SolveContext,
    ) -> AgentFinalOutput:
        artifacts = context.artifacts
        calls = ModelCalls(
            settings=context.settings,
            reviewer=self._reviewer_provider,
            usage_writer=artifacts.write_usage,
            run_id=context.prepared_run.run_id,
        )
        plans = SolverPlanSession(artifacts)
        research = self._research_service
        verifier = Verifier(
            repository=context.repository,
            artifacts=artifacts,
            max_log_chars=context.settings.max_verification_log_chars,
        )
        self._preflight(issue_text=issue_text, context=context)
        logger.info(
            "Sage solve: started run=%s graph=%s profile=%s "
            "nodes=solver,reviewer models=solver,reviewer",
            context.prepared_run.run_id,
            SOLVE_GRAPH_NAME,
            _MODEL_PROFILE_LABEL,
        )

        try:
            solver_result = await self._solver.run(
                stage="solver",
                message=build_solver_message(
                    base_sha=context.prepared_run.base_sha,
                    issue_text=issue_text,
                    memory_context=(
                        context.memory.initial_context if context.memory else None
                    ),
                ),
                context=context,
                plans=plans,
                calls=calls,
                research=research,
            )
            review_version = 0
            verification_pass = 0
            prior_progress: tuple[str, str] | None = None

            while True:
                validate_solver_final(solver_result, plan=plans.saved)
                assert plans.saved is not None
                unknown_research = [
                    result_id
                    for result_id in plans.saved.plan.research_result_ids
                    if research.get_result(result_id) is None
                ]
                if unknown_research:
                    raise InvalidModelContractError(
                        "Solver plan references unknown research results: "
                        + ", ".join(sorted(unknown_research))
                    )
                artifacts.write_solver_final(solver_result)
                solver_outcome = solver_terminal(solver_result, calls=calls)
                if solver_outcome is not None:
                    if (
                        solver_outcome.outcome is SolveOutcome.NO_CHANGE
                        and context.repository.get_changed_files()
                    ):
                        solver_outcome = terminal(
                            SolveOutcome.UNRESOLVED,
                            "Solver reported no change after modifying the candidate.",
                            calls,
                        )
                    return self._persist_terminal(
                        solver_outcome, context, calls, research
                    )
                snapshot = create_candidate_snapshot(
                    repository=context.repository,
                    base_sha=context.prepared_run.base_sha,
                    plan=plans.saved,
                    solver_result=solver_result,
                    max_diff_chars=context.settings.max_candidate_diff_chars,
                )
                artifacts.write_candidate_snapshot(snapshot)
                verification_pass += 1
                logger.info("Verifier: started pass=%d", verification_pass)
                verification = verifier.verify_plan(
                    plans.saved.plan,
                    context.settings,
                    pass_number=verification_pass,
                )
                logger.info(
                    "Verifier: finished pass=%d status=%s "
                    "passing_checks=%d total_checks=%d",
                    verification_pass,
                    verification.status.value,
                    verification.passing_check_count,
                    len(verification.checks),
                )
                if verification.candidate_diff_digest != snapshot.diff_digest:
                    raise AgentRuntimeError(
                        "Candidate changed during deterministic verification."
                    )
                if verification.status is not VerificationStatus.PASS:
                    fingerprint = verification_fingerprint(verification)
                    progress = (snapshot.diff_digest, fingerprint)
                    if progress == prior_progress or not calls.has_time_for_model_call():
                        final = terminal(
                            SolveOutcome.VERIFICATION_FAILED,
                            "The candidate did not pass required deterministic verification.",
                            calls,
                        )
                        return self._persist_terminal(final, context, calls, research)
                    prior_progress = progress
                    solver_result = await self._solver.run(
                        stage="solver-repair",
                        message=build_repair_message(
                            issue_text=issue_text,
                            plan_json=plans.saved.model_dump_json(indent=2),
                            candidate_diff=snapshot.diff,
                            findings_json=verification.model_dump_json(indent=2),
                        ),
                        context=context,
                        plans=plans,
                        calls=calls,
                        research=research,
                    )
                    continue

                review_version += 1
                review = await self._reviewer.review(
                    issue_text=issue_text,
                    snapshot=snapshot,
                    verification=verification,
                    plan=plans.saved,
                    calls=calls,
                    rereview=review_version > 1,
                    research_summary_json=research.summary().model_dump_json(indent=2),
                )
                artifacts.write_review(review, version=review_version)
                if review.verdict is ReviewVerdict.PASS:
                    ensure_candidate_unchanged(
                        repository=context.repository,
                        base_sha=snapshot.base_sha,
                        diff_digest=snapshot.diff_digest,
                    )
                    final = AgentFinalOutput(
                        summary=solver_result.summary,
                        outcome=SolveOutcome.COMPLETED,
                        remaining_uncertainty=[
                            *solver_result.remaining_uncertainty,
                            *verification.uncertainty,
                            *review.uncertainty,
                        ],
                        provenance=calls.provenance(),
                    )
                    return self._persist_terminal(final, context, calls, research)

                if review.verdict is ReviewVerdict.UNCERTAIN:
                    final = terminal(
                        SolveOutcome.HUMAN_REQUIRED_AFTER_START,
                        "Independent review could not establish that the candidate is safe.",
                        calls,
                    )
                    return self._persist_terminal(final, context, calls, research)
                if review.failure_type not in {
                    ReviewFailureType.IMPLEMENTATION,
                    ReviewFailureType.PLANNING,
                    ReviewFailureType.VERIFICATION,
                }:
                    final = review_failure_terminal(review, calls)
                    return self._persist_terminal(final, context, calls, research)

                progress = (snapshot.diff_digest, review_fingerprint(review))
                if progress == prior_progress or not calls.has_time_for_model_call():
                    final = terminal(
                        SolveOutcome.REVIEW_FAILED,
                        "Independent review found blocking issues without further progress.",
                        calls,
                    )
                    return self._persist_terminal(final, context, calls, research)
                prior_progress = progress
                solver_result = await self._solver.run(
                    stage="solver-repair",
                    message=build_repair_message(
                        issue_text=issue_text,
                        plan_json=plans.saved.model_dump_json(indent=2),
                        candidate_diff=snapshot.diff,
                        findings_json=json.dumps(
                            [
                                finding.model_dump(mode="json")
                                for finding in review.blocking_findings
                            ],
                            indent=2,
                        ),
                    ),
                    context=context,
                    plans=plans,
                    calls=calls,
                    research=research,
                )
        except Exception as error:
            final = failure_terminal(error, calls)
            if final is None:
                raise
        final = self._persist_terminal(final, context, calls, research)
        logger.info(
            "Sage solve: finished run=%s outcome=%s model_calls=%d",
            context.prepared_run.run_id,
            final.outcome.value,
            len(calls.records),
        )
        return final

    def _preflight(self, *, issue_text: str, context: SolveContext) -> None:
        if not issue_text.strip():
            raise AgentRuntimeError("Issue context is empty.")
        workspace = context.prepared_run.workspace_dir
        if not workspace.is_dir() or not (workspace / ".git").is_dir():
            raise AgentRuntimeError("Prepared workspace is unavailable.")
        if context.repository.get_changed_files():
            raise AgentRuntimeError("Prepared workspace is not clean at preflight.")

    def _persist_terminal(
        self,
        final: AgentFinalOutput,
        context: SolveContext,
        calls: ModelCalls,
        research: ResearchService,
    ) -> AgentFinalOutput:
        artifacts = context.artifacts
        updated = final.model_copy(update={"provenance": calls.provenance()})
        artifacts.write_usage(updated.provenance)
        research_summary = research.summary()
        if research_summary.searches or research_summary.errors or research_summary.sources:
            artifacts.write_research_summary(research_summary)
        artifacts.write_terminal(updated)
        logger.info(
            "Sage solve: terminal outcome=%s model_calls=%d",
            updated.outcome.value,
            len(calls.records),
        )
        return updated
