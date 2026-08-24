"""Admission, tool-driven Solver, and independent Reviewer V2 runtime."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Sequence
from typing import TypeVar

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableLambda
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI
from langgraph.errors import GraphRecursionError
from openai import APIStatusError, AuthenticationError, RateLimitError
from pydantic import BaseModel, ValidationError

from sage.artifacts.v2 import V2ArtifactStore
from sage.config import Settings
from sage.domain.admission import (
    AdmissionContextSnapshot,
    AdmissionResult,
    ReadinessDisposition,
)
from sage.domain.results import AgentFinalOutput, SolveOutcome
from sage.domain.review import ReviewFailureType, ReviewResult, ReviewVerdict
from sage.domain.runtime import RuntimeContext
from sage.domain.solver import CandidateSnapshot, SolverFinalResult, SolverOutcome, SolverPlan
from sage.domain.usage import AttemptKind, ModelRole
from sage.domain.verification import VerificationResult, VerificationStatus
from sage.errors import AgentRuntimeError
from sage.observability import agent_trace_config, log_agent_result, workflow_trace_config
from sage.providers.errors import ProviderErrorCategory, ProviderInvocationError
from sage.providers.factory import ProviderSet, build_constrained_provider_set
from sage.providers.manager import FinalizationReserveError, ModelCallManager
from sage.research import ResearchService, build_research_service
from sage.runtimes.v2.admission import (
    AdmissionContextSession,
    build_admission_tools,
    clarification_limit_reached,
    next_clarification_round,
    render_admission_context,
    validate_admission_result,
)
from sage.runtimes.langgraph.graph import build_graph as build_tool_graph
from sage.runtimes.langgraph.runtime import is_openai_quota_error, recursion_limit
from sage.runtimes.v2.graph import (
    GRAPH_NAME,
    create_candidate_snapshot,
    review_fingerprint,
)
from sage.runtimes.v2.prompts import (
    ADMISSION_INSTRUCTIONS,
    REVIEWER_INSTRUCTIONS,
    SOLVER_INSTRUCTIONS,
    build_admission_message,
    build_repair_message,
    build_review_message,
    build_solver_message,
)
from sage.runtimes.v2.tools import SolverPlanSession, build_solver_tools
from sage.runtimes.v2.validation import (
    InvalidModelContractError,
    validate_review,
    validate_solver_final,
    validate_solver_plan_context,
)
from sage.verification import Verifier, discover_solver_verification_commands

logger = logging.getLogger(__name__)
OutputModel = TypeVar("OutputModel", bound=BaseModel)


class V2GraphRuntime:
    """Persist read-only context, solve through tools, and review independently."""

    def __init__(
        self,
        settings: Settings,
        providers: ProviderSet | None = None,
        solver_model: BaseChatModel | None = None,
        research_service: ResearchService | None = None,
    ) -> None:
        self._settings = settings
        self._providers = providers or build_constrained_provider_set(settings)
        self._solver_model = solver_model or ChatOpenAI(
            model=settings.v2_solver_model,
            api_key=settings.openai_api_key,
            max_retries=settings.openai_max_retries,
            timeout=float(settings.model_request_timeout_seconds),
            use_responses_api=True,
        )
        self._research_service = research_service

    async def solve(
        self,
        *,
        issue_text: str,
        context: RuntimeContext,
    ) -> AgentFinalOutput:
        """Run V2 under one parent trace with nested Solver/Reviewer work."""

        async def workflow(_: dict[str, object]) -> AgentFinalOutput:
            return await self._solve(issue_text=issue_text, context=context)

        return await RunnableLambda(workflow).ainvoke(
            {},
            config=workflow_trace_config(
                run_id=context.prepared_run.run_id,
                graph_name=GRAPH_NAME,
                model_profile=self._settings.model_profile,
            ),
        )

    async def _solve(
        self,
        *,
        issue_text: str,
        context: RuntimeContext,
    ) -> AgentFinalOutput:
        artifacts = V2ArtifactStore(context.prepared_run.run_dir)
        calls = ModelCallManager(
            settings=self._settings,
            providers=self._providers,
            usage_writer=artifacts.write_usage,
            run_id=context.prepared_run.run_id,
        )
        plans = SolverPlanSession(artifacts)
        research = self._research_service or build_research_service(self._settings)
        verifier = Verifier(
            repository=context.repository,
            artifacts=artifacts,
            max_log_chars=self._settings.max_verification_log_chars,
        )
        self._preflight(issue_text=issue_text, context=context)
        logger.info(
            "V2 workflow: started run=%s graph=%s profile=%s "
            "nodes=admission,solver,reviewer models=solver,reviewer",
            context.prepared_run.run_id,
            GRAPH_NAME,
            self._settings.model_profile,
        )

        try:
            admission_context: AdmissionContextSnapshot | None = None
            admission_context_json: str | None = None
            review_admission_context_json: str | None = None
            if self._settings.v2_admission_enabled:
                admission_session = AdmissionContextSession(
                    context=context,
                    issue_text=issue_text,
                    artifacts=artifacts,
                    research=research,
                )
                clarification_round = next_clarification_round(
                    issue_text,
                    maximum=self._settings.max_clarification_rounds,
                )
                admission = await self._run_admission(
                    message=build_admission_message(
                        base_sha=context.prepared_run.base_sha,
                        issue_text=issue_text,
                        clarification_round=clarification_round,
                    ),
                    context=context,
                    session=admission_session,
                    research=research,
                    calls=calls,
                )
                admission_context = validate_admission_result(
                    admission,
                    session=admission_session,
                    issue_text=issue_text,
                )
                artifacts.write_admission_final(admission)
                log_agent_result(
                    logger,
                    role=ModelRole.ADMISSION,
                    details=(
                        ("Decision", admission.disposition.value),
                        ("Evidence", len(admission_context.evidence)),
                        (
                            "Questions",
                            len(admission.clarification.questions)
                            if admission.clarification
                            else 0,
                        ),
                        ("Context", admission_context.digest[:12]),
                    ),
                )
                terminal = _admission_terminal(
                    admission,
                    issue_text=issue_text,
                    settings=self._settings,
                    artifacts=artifacts,
                    calls=calls,
                )
                if terminal is not None:
                    return self._persist_terminal(terminal, artifacts, calls, research)
                admission_context_json = render_admission_context(
                    admission_context,
                    max_chars=self._settings.v2_admission_context_chars,
                )
                review_admission_context_json = render_admission_context(
                    admission_context,
                    max_chars=min(12_000, self._settings.reviewer_input_chars // 4),
                )
            solver_result = await self._run_solver(
                stage="solver",
                message=build_solver_message(
                    base_sha=context.prepared_run.base_sha,
                    issue_text=issue_text,
                    admission_context_json=admission_context_json,
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
                validate_solver_plan_context(plans.saved, admission=admission_context)
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
                terminal = _solver_terminal(solver_result, calls=calls)
                if terminal is not None:
                    if (
                        terminal.outcome is SolveOutcome.NO_CHANGE
                        and context.repository.get_changed_files()
                    ):
                        terminal = _terminal(
                            SolveOutcome.UNRESOLVED,
                            "Solver reported no change after modifying the candidate.",
                            calls,
                        )
                    return self._persist_terminal(terminal, artifacts, calls, research)
                snapshot = create_candidate_snapshot(
                    repository=context.repository,
                    base_sha=context.prepared_run.base_sha,
                    plan=plans.saved,
                    solver_result=solver_result,
                    max_diff_chars=self._settings.max_candidate_diff_chars,
                )
                artifacts.write_candidate_snapshot(snapshot)
                verification_pass += 1
                verification = self._verify(
                    verifier=verifier,
                    snapshot=snapshot,
                    pass_number=verification_pass,
                    plan=plans.saved.plan,
                )
                if verification.status is not VerificationStatus.PASS:
                    fingerprint = _verification_fingerprint(verification)
                    progress = (snapshot.diff_digest, fingerprint)
                    if progress == prior_progress or not calls.has_time_for_model_call():
                        final = _terminal(
                            SolveOutcome.VERIFICATION_FAILED,
                            "The candidate did not pass required deterministic verification.",
                            calls,
                        )
                        return self._persist_terminal(final, artifacts, calls, research)
                    prior_progress = progress
                    solver_result = await self._run_solver(
                        stage="solver-repair",
                        message=build_repair_message(
                            issue_text=issue_text,
                            plan_json=plans.saved.model_dump_json(indent=2),
                            candidate_diff=snapshot.diff,
                            findings_json=verification.model_dump_json(indent=2),
                            admission_context_json=admission_context_json,
                        ),
                        context=context,
                        plans=plans,
                        calls=calls,
                        research=research,
                    )
                    continue

                review_version += 1
                review = await self._review(
                    issue_text=issue_text,
                    snapshot=snapshot,
                    verification=verification,
                    plan_json=plans.saved.model_dump_json(indent=2),
                    calls=calls,
                    rereview=review_version > 1,
                    admission_context_json=review_admission_context_json,
                    research_summary_json=research.summary().model_dump_json(indent=2),
                )
                validate_review(review, plan=plans.saved)
                artifacts.write_review(review, version=review_version)
                log_agent_result(
                    logger,
                    role=ModelRole.REVIEWER,
                    details=(
                        ("Decision", review.verdict.value),
                        ("Blocking findings", len(review.blocking_findings)),
                        ("Confidence", review.confidence),
                    ),
                )
                if review.verdict is ReviewVerdict.PASS:
                    self._final_candidate_guard(context=context, snapshot=snapshot)
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
                    return self._persist_terminal(final, artifacts, calls, research)

                if review.verdict is ReviewVerdict.UNCERTAIN:
                    final = _terminal(
                        SolveOutcome.HUMAN_REQUIRED_AFTER_START,
                        "Independent review could not establish that the candidate is safe.",
                        calls,
                    )
                    return self._persist_terminal(final, artifacts, calls, research)
                if review.failure_type not in {
                    ReviewFailureType.IMPLEMENTATION,
                    ReviewFailureType.PLANNING,
                    ReviewFailureType.VERIFICATION,
                }:
                    final = _review_failure_terminal(review, calls)
                    return self._persist_terminal(final, artifacts, calls, research)

                progress = (snapshot.diff_digest, review_fingerprint(review))
                if progress == prior_progress or not calls.has_time_for_model_call():
                    final = _terminal(
                        SolveOutcome.REVIEW_FAILED,
                        "Independent review found blocking issues without further progress.",
                        calls,
                    )
                    return self._persist_terminal(final, artifacts, calls, research)
                prior_progress = progress
                solver_result = await self._run_solver(
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
                        admission_context_json=admission_context_json,
                    ),
                    context=context,
                    plans=plans,
                    calls=calls,
                    research=research,
                )
        except asyncio.CancelledError:
            raise
        except ProviderInvocationError as error:
            final = _provider_terminal(error, calls)
        except AuthenticationError:
            final = _terminal(
                SolveOutcome.PROVIDER_UNAVAILABLE,
                "OpenAI rejected the configured V2 coding API key or authorization.",
                calls,
            )
        except RateLimitError as error:
            outcome = (
                SolveOutcome.PROVIDER_UNAVAILABLE
                if is_openai_quota_error(error)
                else SolveOutcome.RATE_LIMITED
            )
            final = _terminal(
                outcome,
                "The V2 coding provider could not serve the run.",
                calls,
            )
        except APIStatusError:
            final = _terminal(
                SolveOutcome.PROVIDER_UNAVAILABLE,
                "The V2 coding provider rejected a required request.",
                calls,
            )
        except FinalizationReserveError:
            final = _terminal(
                SolveOutcome.BUDGET_EXHAUSTED,
                "Sage reached the V2 run deadline finalization reserve.",
                calls,
            )
        except (ValidationError, InvalidModelContractError):
            final = _terminal(
                SolveOutcome.INVALID_MODEL_OUTPUT,
                "A V2 role returned an invalid structured result.",
                calls,
            )
        except GraphRecursionError:
            final = _terminal(
                SolveOutcome.UNRESOLVED,
                "A V2 coding node exhausted its configured tool-loop turn limit.",
                calls,
            )
        except AgentRuntimeError:
            final = _terminal(
                SolveOutcome.UNRESOLVED,
                "V2 could not produce and review a stable authoritative candidate.",
                calls,
            )
        final = self._persist_terminal(final, artifacts, calls, research)
        logger.info(
            "V2 workflow: finished run=%s outcome=%s model_calls=%d",
            context.prepared_run.run_id,
            final.outcome.value,
            len(calls.records),
        )
        return final

    async def _run_solver(
        self,
        *,
        stage: str,
        message: str,
        context: RuntimeContext,
        plans: SolverPlanSession,
        calls: ModelCallManager,
        research: ResearchService,
    ) -> SolverFinalResult:
        input_cap = (
            self._settings.repair_input_chars
            if stage == "solver-repair"
            else self._settings.solver_input_chars
        )
        if len(message) > input_cap:
            raise AgentRuntimeError("Solver context exceeds the configured safe input cap.")
        parsed = await self._run_coding_graph(
            role=ModelRole.SOLVER,
            stage=stage,
            message=message,
            context=context,
            calls=calls,
            tools=build_solver_tools(context, plans, research),
            instructions=SOLVER_INSTRUCTIONS,
            output_schema=SolverFinalResult,
            max_turns=self._settings.max_turns,
        )
        log_agent_result(
            logger,
            role=ModelRole.SOLVER,
            details=(
                ("Decision", parsed.outcome.value),
                ("Plan version", parsed.plan_version),
                ("Verification claims", len(parsed.verification_claims)),
            ),
        )
        return parsed

    async def _run_admission(
        self,
        *,
        message: str,
        context: RuntimeContext,
        session: AdmissionContextSession,
        research: ResearchService,
        calls: ModelCallManager,
    ) -> AdmissionResult:
        if len(message) > self._settings.solver_input_chars:
            raise AgentRuntimeError("Admission input exceeds the configured safe cap.")
        return await self._run_coding_graph(
            role=ModelRole.ADMISSION,
            stage="admission",
            message=message,
            context=context,
            calls=calls,
            tools=build_admission_tools(context, session, research),
            instructions=ADMISSION_INSTRUCTIONS,
            output_schema=AdmissionResult,
            max_turns=self._settings.v2_admission_max_turns,
        )

    async def _run_coding_graph(
        self,
        *,
        role: ModelRole,
        stage: str,
        message: str,
        context: RuntimeContext,
        calls: ModelCallManager,
        tools: Sequence[BaseTool],
        instructions: str,
        output_schema: type[OutputModel],
        max_turns: int,
    ) -> OutputModel:
        """Run the shared sequential OpenAI tool loop for Admission or Solver."""

        calls.start_coding_session(role=role)
        model = self._solver_model.bind_tools(
            tools,
            response_format=output_schema,
            parallel_tool_calls=False,
            strict=False,
        )

        def start(_: int) -> object:
            return calls.start_coding_call(role=role, stage=stage)

        def finish(token: object, response, duration_ms: float) -> None:
            calls.finish_coding_call(
                role=role,
                stage=stage,
                call_number=int(token),
                message=response,
                latency_ms=duration_ms,
            )

        def fail(token: object, error: BaseException, duration_ms: float) -> None:
            calls.fail_coding_call(
                role=role,
                stage=stage,
                call_number=int(token),
                error=error,
                latency_ms=duration_ms,
            )

        graph = build_tool_graph(
            model=model,
            tools=tools,
            max_turns=max_turns,
            instructions=instructions,
            output_schema=output_schema,
            graph_name=f"{GRAPH_NAME}_{stage.replace('-', '_')}",
            role_name=role.value.capitalize(),
            on_model_start=start,
            on_model_finish=finish,
            on_model_error=fail,
        )
        result = await graph.ainvoke(
            {"messages": [HumanMessage(content=message)], "model_turns": 0},
            config={
                **agent_trace_config(
                    run_id=context.prepared_run.run_id,
                    role=role,
                    stage=stage,
                    attempt=AttemptKind.PRIMARY,
                    provider="openai",
                    model=self._settings.v2_solver_model,
                    call_number=len(calls.records) + 1,
                ),
                "recursion_limit": recursion_limit(max_turns),
            },
        )
        return output_schema.model_validate(result.get("final_output"))

    async def _review(
        self,
        *,
        issue_text: str,
        snapshot: CandidateSnapshot,
        verification: VerificationResult,
        plan_json: str,
        calls: ModelCallManager,
        rereview: bool,
        admission_context_json: str | None,
        research_summary_json: str,
    ) -> ReviewResult:
        packet = build_review_message(
            issue_text=issue_text,
            plan_json=plan_json,
            changed_files_json=json.dumps(snapshot.changed_files, indent=2),
            candidate_diff=snapshot.diff,
            verification_json=verification.model_dump_json(indent=2),
            solver_summary=snapshot.solver_summary,
            admission_context_json=admission_context_json,
            research_summary_json=research_summary_json,
        )
        if len(packet) > self._settings.reviewer_input_chars:
            raise AgentRuntimeError(
                "Reviewer context exceeds the configured safe input cap."
            )
        result = await calls.invoke_reviewer(
            stage="rereview" if rereview else "review",
            messages=[
                SystemMessage(content=REVIEWER_INSTRUCTIONS),
                HumanMessage(content=packet),
            ],
            schema=ReviewResult,
        )
        return ReviewResult.model_validate(result.parsed)

    def _verify(
        self,
        *,
        verifier: Verifier,
        snapshot: CandidateSnapshot,
        pass_number: int,
        plan: SolverPlan,
    ) -> VerificationResult:
        commands = discover_solver_verification_commands(
            plan=plan,
            settings=self._settings,
        )
        logger.info("Verifier: started pass=%d checks=%d", pass_number, len(commands))
        verification = verifier.verify(commands, pass_number=pass_number)
        logger.info(
            "Verifier: finished pass=%d status=%s passing_checks=%d total_checks=%d",
            pass_number,
            verification.status.value,
            verification.passing_check_count,
            len(verification.checks),
        )
        if verification.candidate_diff_digest != snapshot.diff_digest:
            raise AgentRuntimeError("Candidate changed during deterministic verification.")
        return verification

    def _final_candidate_guard(
        self,
        *,
        context: RuntimeContext,
        snapshot: CandidateSnapshot,
    ) -> None:
        if context.repository.get_head_sha() != snapshot.base_sha:
            raise AgentRuntimeError("Candidate HEAD changed after independent review.")
        current = context.repository.get_complete_diff()
        if hashlib.sha256(current.encode("utf-8")).hexdigest() != snapshot.diff_digest:
            raise AgentRuntimeError("Candidate changed after independent review.")

    def _preflight(self, *, issue_text: str, context: RuntimeContext) -> None:
        if not issue_text.strip():
            raise AgentRuntimeError("V2 Issue context is empty.")
        if self._settings.runtime != "v2-prototype":
            raise AgentRuntimeError("V2 runtime was selected with invalid settings.")
        workspace = context.prepared_run.workspace_dir
        if not workspace.is_dir() or not (workspace / ".git").is_dir():
            raise AgentRuntimeError("V2 prepared workspace is unavailable.")
        if context.repository.get_changed_files():
            raise AgentRuntimeError("V2 prepared workspace is not clean at preflight.")

    def _persist_terminal(
        self,
        final: AgentFinalOutput,
        artifacts: V2ArtifactStore,
        calls: ModelCallManager,
        research: ResearchService,
    ) -> AgentFinalOutput:
        updated = final.model_copy(update={"provenance": calls.provenance()})
        artifacts.write_usage(updated.provenance)
        research_summary = research.summary()
        if research_summary.searches or research_summary.errors or research_summary.sources:
            artifacts.write_research_summary(research_summary)
        artifacts.write_terminal(updated)
        logger.info(
            "V2 workflow: terminal outcome=%s model_calls=%d",
            updated.outcome.value,
            len(calls.records),
        )
        return updated


def _admission_terminal(
    result: AdmissionResult,
    *,
    issue_text: str,
    settings: Settings,
    artifacts: V2ArtifactStore,
    calls: ModelCallManager,
) -> AgentFinalOutput | None:
    if result.disposition is ReadinessDisposition.READY:
        return None
    if result.disposition is ReadinessDisposition.ENVIRONMENT_BLOCKED:
        return _terminal(SolveOutcome.ENVIRONMENT_BLOCKED, result.summary, calls)
    if result.disposition is ReadinessDisposition.UNSUPPORTED:
        return _terminal(SolveOutcome.UNSUPPORTED, result.summary, calls)
    if clarification_limit_reached(
        issue_text,
        maximum=settings.max_clarification_rounds,
    ):
        return _terminal(
            SolveOutcome.NEEDS_MAINTAINER_REWRITE,
            "The Issue still lacks required context after the configured "
            "clarification rounds; rewrite the Issue with the requested "
            "contract before retrying.",
            calls,
        )
    packet = result.clarification
    if packet is None:
        raise InvalidModelContractError("Human-required Admission omitted clarification.")
    round_number = next_clarification_round(
        issue_text,
        maximum=settings.max_clarification_rounds,
    )
    instruction = (
        "Rewrite the Issue with the requested contract, then create one new "
        "exact `/sage solve` or `/sage fix` comment."
        if round_number == settings.max_clarification_rounds
        else "Reply with the requested information, then create one new exact "
        "`/sage solve` or `/sage fix` comment."
    )
    packet = packet.model_copy(
        update={"round": round_number, "rerun_instruction": instruction}
    )
    artifacts.write_clarification(packet)
    outcome = (
        SolveOutcome.NEEDS_HUMAN_INFORMATION
        if result.disposition is ReadinessDisposition.NEEDS_HUMAN_INFORMATION
        else SolveOutcome.NEEDS_HUMAN_DESIGN_DECISION
    )
    return AgentFinalOutput(
        summary=result.summary,
        outcome=outcome,
        clarification=packet,
        provenance=calls.provenance(),
    )


def _solver_terminal(
    result: SolverFinalResult,
    *,
    calls: ModelCallManager,
) -> AgentFinalOutput | None:
    mapping = {
        SolverOutcome.NO_CHANGE: SolveOutcome.NO_CHANGE,
        SolverOutcome.BLOCKED: SolveOutcome.HUMAN_REQUIRED_AFTER_START,
        SolverOutcome.UNRESOLVED: SolveOutcome.UNRESOLVED,
    }
    outcome = mapping.get(result.outcome)
    if outcome is None:
        return None
    return AgentFinalOutput(
        summary=result.summary,
        outcome=outcome,
        remaining_uncertainty=list(result.remaining_uncertainty),
        provenance=calls.provenance(),
    )


def _review_failure_terminal(
    review: ReviewResult,
    calls: ModelCallManager,
) -> AgentFinalOutput:
    if review.failure_type is ReviewFailureType.ENVIRONMENT:
        outcome = SolveOutcome.ENVIRONMENT_BLOCKED
    elif review.failure_type is ReviewFailureType.REQUIREMENT_AMBIGUITY:
        outcome = SolveOutcome.HUMAN_REQUIRED_AFTER_START
    else:
        outcome = SolveOutcome.REVIEW_FAILED
    return _terminal(outcome, "Independent review rejected the candidate.", calls)


def _provider_terminal(
    error: ProviderInvocationError,
    calls: ModelCallManager,
) -> AgentFinalOutput:
    if error.category is ProviderErrorCategory.RATE_LIMITED:
        outcome = SolveOutcome.RATE_LIMITED
    elif error.category is ProviderErrorCategory.SCHEMA_ERROR:
        outcome = SolveOutcome.INVALID_MODEL_OUTPUT
    else:
        outcome = SolveOutcome.PROVIDER_UNAVAILABLE
    return _terminal(
        outcome,
        f"The configured {error.provider} provider was unavailable for its V2 role.",
        calls,
    )


def _terminal(
    outcome: SolveOutcome,
    summary: str,
    calls: ModelCallManager,
) -> AgentFinalOutput:
    return AgentFinalOutput(summary=summary, outcome=outcome, provenance=calls.provenance())


def _verification_fingerprint(result: VerificationResult) -> str:
    failures = [
        check.fingerprint
        for check in result.checks
        if check.status in {VerificationStatus.FAIL, VerificationStatus.TIMEOUT}
    ]
    return "|".join(failures)
