"""Independent read-only Reviewer role."""

from __future__ import annotations

import json
import logging

from langchain_core.messages import HumanMessage, SystemMessage

from sage.agents.prompts import REVIEWER_INSTRUCTIONS
from sage.harness.context.packets import build_review_message
from sage.harness.context.instructions import with_repository_instructions
from sage.config import Settings
from sage.domain.review import ReviewResult, ReviewVerdict
from sage.domain.solver import CandidateSnapshot, SavedSolverPlan
from sage.domain.usage import ModelRole
from sage.domain.verification import VerificationResult
from sage.errors import InvalidModelContractError
from sage.observability import log_agent_result
from sage.providers.calls import ModelCalls

logger = logging.getLogger(__name__)


class ReviewerAgent:
    """Build one bounded review packet and validate the returned contract."""

    def __init__(self, *, settings: Settings) -> None:
        self._settings = settings

    async def review(
        self,
        *,
        issue_text: str,
        snapshot: CandidateSnapshot,
        verification: VerificationResult,
        plan: SavedSolverPlan,
        calls: ModelCalls,
        rereview: bool,
        repository_instructions: str = "",
    ) -> ReviewResult:
        packet = build_review_message(
            issue_text=issue_text,
            plan_json=plan.model_dump_json(indent=2),
            changed_files_json=json.dumps(snapshot.changed_files, indent=2),
            candidate_diff=snapshot.diff,
            verification_json=verification.model_dump_json(indent=2),
            solver_summary=snapshot.solver_summary,
            max_chars=self._settings.reviewer_input_chars,
        )
        result = await calls.invoke_reviewer(
            stage="rereview" if rereview else "review",
            messages=[
                SystemMessage(content=with_repository_instructions(REVIEWER_INSTRUCTIONS, repository_instructions)),
                HumanMessage(content=packet),
            ],
            schema=ReviewResult,
        )
        review = ReviewResult.model_validate(result.parsed)
        validate_review(review, plan=plan)
        log_agent_result(
            logger,
            role=ModelRole.REVIEWER,
            details=(
                ("Decision", review.verdict.value),
                ("Blocking findings", len(review.blocking_findings)),
                ("Confidence", review.confidence),
            ),
        )
        return review


def validate_review(review: ReviewResult, *, plan: SavedSolverPlan) -> None:
    """Require a passing review to cover every planned criterion exactly once."""

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
