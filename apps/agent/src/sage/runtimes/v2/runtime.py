"""Tool-driven Solver and independent Reviewer V2 runtime."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Sequence
from dataclasses import replace
from time import monotonic
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
from sage.domain.results import AgentFinalOutput, SolveOutcome
from sage.domain.review import ReviewFailureType, ReviewResult, ReviewVerdict
from sage.domain.runtime import RuntimeContext
from sage.domain.solver import CandidateSnapshot, SolverFinalResult, SolverOutcome, SolverPlan
from sage.domain.usage import AttemptKind, ModelRole
from sage.domain.verification import VerificationResult, VerificationStatus
from sage.errors import AgentRuntimeError
from sage.memory.engine import build_memory_engine
from sage.memory.canonical import canonical_digest
from sage.memory.models import (
    MemoryFailure,
    MemoryMode,
    MemoryRunReport,
    MemoryRunRequest,
)
from sage.observability import agent_trace_config, log_agent_result, workflow_trace_config
from sage.providers.errors import ProviderErrorCategory, ProviderInvocationError
from sage.providers.factory import ProviderSet, build_constrained_provider_set
from sage.providers.manager import FinalizationReserveError, ModelCallManager
from sage.providers.openai import is_openai_quota_error
from sage.research import ResearchService, build_research_service
from sage.runtimes.tool_loop import build_graph as build_tool_graph
from sage.runtimes.tool_loop import recursion_limit
from sage.runtimes.v2.graph import (
    GRAPH_NAME,
    create_candidate_snapshot,
    review_fingerprint,
)
from sage.runtimes.v2.prompts import (
    REVIEWER_INSTRUCTIONS,
    SOLVER_INSTRUCTIONS,
    build_repair_message,
    build_review_message,
    build_solver_message,
)
from sage.runtimes.v2.tools import SolverPlanSession, build_solver_tools
from sage.runtimes.v2.validation import (
    InvalidModelContractError,
    validate_review,
    validate_solver_final,
)
from sage.verification import Verifier, discover_solver_verification_commands

logger = logging.getLogger(__name__)
OutputModel = TypeVar("OutputModel", bound=BaseModel)


class V2GraphRuntime:
    """Solve through tools, verify deterministically, and review independently."""

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

        workflow_started_at = monotonic()

        async def workflow(_: dict[str, object]) -> AgentFinalOutput:
            if not self._settings.memory_enabled:
                logger.info(
                    "memory disabled run=%s",
                    context.prepared_run.run_id,
                )
                return await self._solve(
                    issue_text=issue_text,
                    context=context,
                    workflow_started_at=workflow_started_at,
                )
            if context.memory_identity is None:
                raise AgentRuntimeError(
                    "Memory-enabled runtime requires a stable repository identity."
                )
            engine = build_memory_engine(self._settings, context.repository)
            session = await engine.begin(
                MemoryRunRequest(
                    identity=context.memory_identity,
                    run_id=context.prepared_run.run_id,
                    target_commit=context.prepared_run.base_sha,
                    workspace_path=context.prepared_run.workspace_dir,
                )
            )
            logger.info(
                "memory startup completed run=%s mode=%s",
                context.prepared_run.run_id,
                session.mode.value,
            )
            effective_context = replace(context, memory_session=session)
            forest = await session.initial_context(issue_text)
            logger.info(
                "memory context supplied to solver run=%s mode=%s files=%d "
                "candidates=%d paths=%r",
                context.prepared_run.run_id,
                session.mode.value,
                len(forest.entries),
                forest.candidate_count,
                [entry.path for entry in forest.entries],
            )
            logger.debug(
                "memory initial source supplied to solver coverage=%r",
                [
                    {
                        "path": entry.path,
                        "lines": entry.included_line_ranges,
                        "chars": len(entry.source or ""),
                    }
                    for entry in forest.entries
                ],
            )
            artifacts = V2ArtifactStore(context.prepared_run.run_dir)
            artifacts.write_context_forest(forest)
            try:
                final = await self._solve(
                    issue_text=issue_text,
                    context=effective_context,
                    memory_context=forest.render_for_solver(),
                    workflow_started_at=workflow_started_at,
                )
                remaining = max(
                    0.1,
                    workflow_started_at
                    + self._settings.run_deadline_seconds
                    - monotonic(),
                )
                try:
                    async with asyncio.timeout(
                        min(
                            float(self._settings.finalization_reserve_seconds),
                            remaining,
                        )
                    ):
                        report = await session.finalize(final.outcome)
                except TimeoutError:
                    report = MemoryRunReport(
                        mode=MemoryMode.FALLBACK,
                        repository_identity_digest=canonical_digest(
                            context.memory_identity
                        ),
                        repository_display_name=context.memory_identity.display_name,
                        target_commit=context.prepared_run.base_sha,
                        failure=MemoryFailure(
                            component="snapshot",
                            stage="finalize",
                            error_code="MemoryFinalizationTimeout",
                            safe_message=(
                                "SMRT finalization exceeded the solve deadline reserve."
                            ),
                            target_commit=context.prepared_run.base_sha,
                        ),
                    )
                updated = final.model_copy(update={"memory": report})
                logger.info(
                    "memory finalized run=%s mode=%s input_snapshot_id=%s "
                    "output_snapshot_id=%s snapshot_published=%s files=%d "
                    "reused_cards=%d created_cards=%d",
                    context.prepared_run.run_id,
                    report.mode.value,
                    (
                        str(report.input_snapshot_id)
                        if report.input_snapshot_id
                        else "none"
                    ),
                    (
                        str(report.output_snapshot_id)
                        if report.output_snapshot_id
                        else "none"
                    ),
                    report.snapshot_published,
                    report.final_file_count,
                    report.reused_cards,
                    report.created_cards,
                )
                artifacts.write_memory_summary(report)
                artifacts.write_terminal(updated)
                return updated
            finally:
                await engine.close()

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
        memory_context: str = "",
        workflow_started_at: float | None = None,
    ) -> AgentFinalOutput:
        artifacts = V2ArtifactStore(context.prepared_run.run_dir)
        calls = ModelCallManager(
            settings=self._settings,
            providers=self._providers,
            usage_writer=artifacts.write_usage,
            run_id=context.prepared_run.run_id,
            workflow_started_at=workflow_started_at,
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
            "nodes=solver,reviewer models=solver,reviewer",
            context.prepared_run.run_id,
            GRAPH_NAME,
            self._settings.model_profile,
        )

        try:
            solver_result = await self._run_solver(
                stage="solver",
                message=build_solver_message(
                    base_sha=context.prepared_run.base_sha,
                    issue_text=issue_text,
                    memory_context=memory_context,
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
                artifacts.write_solver_final(solver_result)
                validate_solver_final(solver_result, plan=plans.saved)
                assert plans.saved is not None
                unknown_research = research.unknown_result_ids(
                    plans.saved.plan.research_result_ids
                )
                if unknown_research:
                    raise InvalidModelContractError(
                        "Solver plan references unknown research results: "
                        + ", ".join(sorted(unknown_research))
                    )
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
        except ValidationError:
            final = _terminal(
                SolveOutcome.INVALID_MODEL_OUTPUT,
                "A V2 role returned an invalid structured result.",
                calls,
            )
        except InvalidModelContractError as error:
            logger.warning(
                "V2 role result contract rejected run=%s reason=%s",
                context.prepared_run.run_id,
                error,
            )
            final = _terminal(
                SolveOutcome.INVALID_MODEL_OUTPUT,
                "A V2 role violated its result contract: " + str(error)[:1_500],
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
        parsed = await self._run_solver_graph(
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

    async def _run_solver_graph(
        self,
        *,
        stage: str,
        message: str,
        context: RuntimeContext,
        calls: ModelCallManager,
        tools: Sequence[BaseTool],
        instructions: str,
        output_schema: type[OutputModel],
        max_turns: int,
    ) -> OutputModel:
        """Run one sequential OpenAI Solver tool loop."""

        calls.start_solver_session()
        model = self._solver_model.bind_tools(
            tools,
            response_format=output_schema,
            parallel_tool_calls=False,
            strict=False,
        )

        def start(_: int) -> object:
            return calls.start_coding_call(role=ModelRole.SOLVER, stage=stage)

        def finish(token: object, response, duration_ms: float) -> None:
            calls.finish_coding_call(
                role=ModelRole.SOLVER,
                stage=stage,
                call_number=int(token),
                message=response,
                latency_ms=duration_ms,
            )

        def fail(token: object, error: BaseException, duration_ms: float) -> None:
            calls.fail_coding_call(
                role=ModelRole.SOLVER,
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
            role_name=ModelRole.SOLVER.value.capitalize(),
            on_model_start=start,
            on_model_finish=finish,
            on_model_error=fail,
        )
        result = await graph.ainvoke(
            {"messages": [HumanMessage(content=message)], "model_turns": 0},
            config={
                **agent_trace_config(
                    run_id=context.prepared_run.run_id,
                    role=ModelRole.SOLVER,
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
        research_summary_json: str,
    ) -> ReviewResult:
        packet = build_review_message(
            issue_text=issue_text,
            plan_json=plan_json,
            changed_files_json=json.dumps(snapshot.changed_files, indent=2),
            candidate_diff=snapshot.diff,
            verification_json=verification.model_dump_json(indent=2),
            solver_summary=snapshot.solver_summary,
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
        if self._settings.runtime != "v2":
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
