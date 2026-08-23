"""Sequential LangGraph topology and deterministic routing for Sage V2."""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from sage.artifacts.v2 import V2ArtifactStore
from sage.config import Settings
from sage.context.compiler import ContextCompiler
from sage.context.models import ContextPacket, RepositoryEvidence
from sage.domain.admission import (
    AutonomyContract,
    ClarificationPacket,
    IntakeResult,
    ReadinessDisposition,
)
from sage.domain.planning import ExecutionPlan
from sage.domain.results import AgentFinalOutput, SolveOutcome
from sage.domain.review import ReviewFailureType, ReviewResult, ReviewVerdict
from sage.domain.usage import ModelRole
from sage.domain.verification import VerificationResult, VerificationStatus
from sage.errors import AgentRuntimeError, RepositoryError
from sage.observability import log_agent_result
from sage.providers.manager import ModelCallManager
from sage.repository import RepositoryTools
from sage.repository.scout import RepositoryMap, RepositoryScout
from sage.repository.scope import paths_outside_scopes
from sage.runtimes.v2.models import SolverResult, SolverStatus
from sage.runtimes.v2.prompts import (
    PLANNER_INSTRUCTIONS,
    REVIEWER_INSTRUCTIONS,
    SOLVER_INSTRUCTIONS,
)
from sage.runtimes.v2.validation import (
    InvalidModelContractError,
    build_autonomy_contract,
    normalize_patch,
    validate_ready_intake,
    validate_retrieval_request,
    validate_review,
)
from sage.verification import Verifier, discover_verification_commands

GRAPH_NAME = "sage_v2_prototype"
logger = logging.getLogger(__name__)


class V2GraphInput(TypedDict):
    issue_text: str


class V2State(TypedDict, total=False):
    issue_text: str
    repository_map: RepositoryMap
    intake: IntakeResult
    plan: ExecutionPlan
    contract: AutonomyContract
    context_ref: str
    readiness_evidence: tuple[RepositoryEvidence, ...]
    solver_evidence: tuple[RepositoryEvidence, ...]
    solver_result: SolverResult
    candidate_error: str
    changed_files: list[str]
    candidate_diff_digest: str
    verification: VerificationResult
    review: ReviewResult
    implementation_repairs: int
    review_repairs: int
    readiness_context_expansions: int
    solver_context_expansions: int
    verification_pass: int
    previous_failure_fingerprint: str
    stuck: bool
    final_output: AgentFinalOutput


class V2GraphOutput(TypedDict):
    final_output: AgentFinalOutput


@dataclass(frozen=True, slots=True)
class V2Services:
    settings: Settings
    repository: RepositoryTools
    scout: RepositoryScout
    compiler: ContextCompiler
    calls: ModelCallManager
    verifier: Verifier
    artifacts: V2ArtifactStore
    base_sha: str
    workspace: Path


def build_graph(
    services: V2Services,
) -> CompiledStateGraph[V2State, None, V2GraphInput, V2GraphOutput]:
    """Compile one checkpoint-free sequential V2 graph."""

    settings = services.settings

    async def preflight(state: V2State) -> dict[str, object]:
        issue = state.get("issue_text", "").strip()
        if not issue:
            raise AgentRuntimeError("V2 Issue context is empty.")
        if settings.runtime != "v2-prototype":
            raise AgentRuntimeError("V2 runtime was selected with invalid settings.")
        if not services.workspace.is_dir() or not (services.workspace / ".git").is_dir():
            raise AgentRuntimeError("V2 prepared workspace is unavailable.")
        if services.repository.get_changed_files():
            raise AgentRuntimeError("V2 prepared workspace is not clean at preflight.")
        return {
            "implementation_repairs": 0,
            "review_repairs": 0,
            "readiness_context_expansions": 0,
            "solver_context_expansions": 0,
            "verification_pass": 0,
            "stuck": False,
        }

    async def scout(state: V2State) -> dict[str, RepositoryMap]:
        repository_map = services.scout.scout(
            issue_text=state["issue_text"],
            base_sha=services.base_sha,
        )
        services.artifacts.write_repository_map(repository_map)
        return {"repository_map": repository_map}

    async def compile_intake(state: V2State) -> dict[str, str]:
        packet = services.compiler.compile_intake(
            issue_text=state["issue_text"],
            repository_map=state["repository_map"],
            clarification_round=_prior_clarification_round(state["issue_text"]),
        )
        return {"context_ref": _persist_context(services, packet)}

    async def intake_planner(state: V2State) -> dict[str, IntakeResult]:
        intake = await _invoke(
            services,
            stage="intake-planner",
            role=ModelRole.PLANNER,
            instructions=PLANNER_INSTRUCTIONS,
            context_ref=state["context_ref"],
            schema=IntakeResult,
        )
        if len(intake.blocking_questions) > settings.max_blocking_questions:
            raise InvalidModelContractError(
                "Planner exceeded the configured blocking-question limit."
            )
        services.artifacts.write_intake(intake)
        if intake.plan is not None:
            services.artifacts.write_plan(intake.plan)
        return {"intake": intake}

    async def readiness_guard(state: V2State) -> dict[str, object]:
        return {}

    async def expand_readiness_context(
        state: V2State,
    ) -> dict[str, object]:
        intake = state["intake"]
        for request in intake.retrieval_requests:
            validate_retrieval_request(request)
        evidence = services.compiler.fulfill_requests(
            intake.retrieval_requests,
            repository_map=state["repository_map"],
        )
        return {
            "readiness_evidence": evidence,
            "readiness_context_expansions": state["readiness_context_expansions"] + 1,
        }

    async def compile_readiness_recheck(state: V2State) -> dict[str, str]:
        packet = services.compiler.compile_readiness_recheck(
            issue_text=state["issue_text"],
            prior=state["intake"],
            evidence=state["readiness_evidence"],
        )
        return {"context_ref": _persist_context(services, packet)}

    async def readiness_recheck(state: V2State) -> dict[str, IntakeResult]:
        intake = await _invoke(
            services,
            stage="readiness-recheck",
            role=ModelRole.PLANNER,
            instructions=PLANNER_INSTRUCTIONS,
            context_ref=state["context_ref"],
            schema=IntakeResult,
        )
        services.artifacts.write_intake(intake)
        if intake.plan is not None:
            services.artifacts.write_plan(intake.plan)
        return {"intake": intake}

    async def autonomy_commit(state: V2State) -> dict[str, object]:
        plan = validate_ready_intake(state["intake"])
        contract = build_autonomy_contract(
            plan=plan,
            base_sha=services.base_sha,
            profile=settings.model_profile,
            model_calls_remaining=services.calls.remaining_calls,
            implementation_repairs_remaining=settings.max_implementation_repairs,
            review_repairs_remaining=settings.max_review_repairs,
            solver_context_expansions_remaining=(
                settings.max_solver_context_expansions
                - state.get("solver_context_expansions", 0)
            ),
        )
        services.artifacts.write_plan(plan)
        services.artifacts.write_autonomy_contract(contract)
        return {"plan": plan, "contract": contract}

    async def compile_solver(state: V2State) -> dict[str, str]:
        packet = services.compiler.compile_solver(
            issue_text=state["issue_text"],
            plan=state["plan"],
            contract=state["contract"],
            repository_map=state["repository_map"],
            additional_evidence=state.get("solver_evidence", ()),
        )
        return {"context_ref": _persist_context(services, packet)}

    async def solver(state: V2State) -> dict[str, SolverResult]:
        result = await _invoke(
            services,
            stage=("solver-retry" if state.get("solver_context_expansions") else "solver"),
            role=ModelRole.SOLVER,
            instructions=SOLVER_INSTRUCTIONS,
            context_ref=state["context_ref"],
            schema=SolverResult,
        )
        return {"solver_result": result}

    async def expand_solver_context(state: V2State) -> dict[str, object]:
        result = state["solver_result"]
        for request in result.retrieval_requests:
            validate_retrieval_request(request)
        evidence = services.compiler.fulfill_requests(
            result.retrieval_requests,
            repository_map=state["repository_map"],
        )
        return {
            "solver_evidence": evidence,
            "solver_context_expansions": state["solver_context_expansions"] + 1,
        }

    async def apply_candidate(state: V2State) -> dict[str, object]:
        return _apply_solver_result(services, state["solver_result"], state["contract"])

    async def hard_verify(state: V2State) -> dict[str, object]:
        pass_number = state["verification_pass"] + 1
        commands = discover_verification_commands(
            repository_map=state["repository_map"],
            plan=state["plan"],
            timeout_seconds=settings.command_timeout_seconds,
            configured=settings.verification_commands,
        )
        logger.info(
            "Verifier: started pass=%d checks=%d",
            pass_number,
            len(commands),
        )
        verification = services.verifier.verify(commands, pass_number=pass_number)
        logger.info(
            "Verifier: finished pass=%d status=%s passing_checks=%d total_checks=%d",
            pass_number,
            verification.status.value,
            verification.passing_check_count,
            len(verification.checks),
        )
        fingerprint = _first_failure_fingerprint(verification)
        stuck = bool(
            state.get("previous_failure_fingerprint")
            and fingerprint
            and fingerprint == state["previous_failure_fingerprint"]
            and verification.candidate_diff_digest == state.get("candidate_diff_digest")
        )
        return {
            "verification": verification,
            "verification_pass": pass_number,
            "previous_failure_fingerprint": fingerprint,
            "candidate_diff_digest": verification.candidate_diff_digest,
            "stuck": stuck,
        }

    async def compile_implementation_repair(state: V2State) -> dict[str, str]:
        reason = state.get("candidate_error") or _verification_reason(state["verification"])
        packet = services.compiler.compile_solver(
            issue_text=state["issue_text"],
            plan=state["plan"],
            contract=state["contract"],
            repository_map=state["repository_map"],
            repair_reason=reason,
            current_diff=services.repository.get_complete_diff(),
            verification=state.get("verification"),
        )
        return {"context_ref": _persist_context(services, packet)}

    async def implementation_repair(state: V2State) -> dict[str, object]:
        result = await _invoke(
            services,
            stage="implementation-repair",
            role=ModelRole.SOLVER,
            instructions=SOLVER_INSTRUCTIONS,
            context_ref=state["context_ref"],
            schema=SolverResult,
        )
        return {
            "solver_result": result,
            "implementation_repairs": state["implementation_repairs"] + 1,
            "candidate_error": "",
            "stuck": False,
        }

    async def compile_review(state: V2State) -> dict[str, str]:
        packet = services.compiler.compile_reviewer(
            issue_text=state["issue_text"],
            contract=state["contract"],
            diff=services.repository.get_complete_diff(),
            changed_files=services.repository.get_changed_files(),
            verification=state["verification"],
            repository_map=state["repository_map"],
        )
        return {"context_ref": _persist_context(services, packet)}

    async def reviewer(state: V2State) -> dict[str, ReviewResult]:
        review = await _invoke(
            services,
            stage=("rereview" if state.get("review_repairs") else "review"),
            role=ModelRole.REVIEWER,
            instructions=REVIEWER_INSTRUCTIONS,
            context_ref=state["context_ref"],
            schema=ReviewResult,
        )
        validate_review(review, plan=state["plan"])
        services.artifacts.write_review(review)
        return {"review": review}

    async def compile_review_repair(state: V2State) -> dict[str, str]:
        packet = services.compiler.compile_solver(
            issue_text=state["issue_text"],
            plan=state["plan"],
            contract=state["contract"],
            repository_map=state["repository_map"],
            repair_reason="Repair only the validated blocking review findings.",
            current_diff=services.repository.get_complete_diff(),
            verification=state["verification"],
            review_findings=state["review"].blocking_findings,
        )
        return {"context_ref": _persist_context(services, packet)}

    async def review_repair(state: V2State) -> dict[str, object]:
        result = await _invoke(
            services,
            stage="review-repair",
            role=ModelRole.SOLVER,
            instructions=SOLVER_INSTRUCTIONS,
            context_ref=state["context_ref"],
            schema=SolverResult,
        )
        return {
            "solver_result": result,
            "review_repairs": state["review_repairs"] + 1,
            "candidate_error": "",
            "stuck": False,
        }

    async def clarification_terminal(state: V2State) -> dict[str, AgentFinalOutput]:
        intake = state["intake"]
        prior_round = _prior_clarification_round(state["issue_text"])
        round_number = prior_round + 1
        if round_number > settings.max_clarification_rounds:
            return _terminal(
                services,
                state,
                outcome=SolveOutcome.NEEDS_MAINTAINER_REWRITE,
                summary=(
                    "Sage could not admit this Issue after the configured "
                    "clarification rounds. Revise the Issue with a complete design."
                ),
            )
        disposition = intake.disposition
        outcome = (
            SolveOutcome.NEEDS_HUMAN_INFORMATION
            if disposition is ReadinessDisposition.NEEDS_HUMAN_INFORMATION
            else SolveOutcome.NEEDS_HUMAN_DESIGN_DECISION
        )
        packet = ClarificationPacket(
            round=round_number,
            disposition=disposition,
            summary=intake.rationale,
            questions=intake.blocking_questions,
            rerun_instruction=(
                "Answer these questions in a new Issue comment, then post a new "
                "exact /sage solve command."
            ),
        )
        return _terminal(
            services,
            state,
            outcome=outcome,
            summary=intake.rationale,
            clarification=packet,
        )

    async def blocked_terminal(state: V2State) -> dict[str, AgentFinalOutput]:
        disposition = state["intake"].disposition
        mapping = {
            ReadinessDisposition.HUMAN_REQUIRED: SolveOutcome.HUMAN_REQUIRED,
            ReadinessDisposition.ENVIRONMENT_BLOCKED: SolveOutcome.ENVIRONMENT_BLOCKED,
            ReadinessDisposition.UNSUPPORTED: SolveOutcome.UNSUPPORTED,
            ReadinessDisposition.NEEDS_REPOSITORY_CONTEXT: SolveOutcome.UNRESOLVED,
        }
        return _terminal(
            services,
            state,
            outcome=mapping.get(disposition, SolveOutcome.UNRESOLVED),
            summary=state["intake"].rationale,
        )

    async def no_change_terminal(state: V2State) -> dict[str, AgentFinalOutput]:
        return _terminal(
            services,
            state,
            outcome=SolveOutcome.NO_CHANGE,
            summary=state["solver_result"].summary,
        )

    async def human_after_start_terminal(state: V2State) -> dict[str, AgentFinalOutput]:
        return _terminal(
            services,
            state,
            outcome=SolveOutcome.HUMAN_REQUIRED_AFTER_START,
            summary=state["solver_result"].summary,
        )

    async def unresolved_terminal(state: V2State) -> dict[str, AgentFinalOutput]:
        summary = state.get("candidate_error") or (
            state.get("solver_result").summary
            if state.get("solver_result") is not None
            else "Sage could not produce a verified candidate within the V2 budget."
        )
        return _terminal(
            services,
            state,
            outcome=SolveOutcome.UNRESOLVED,
            summary=summary,
        )

    async def verification_terminal(state: V2State) -> dict[str, AgentFinalOutput]:
        return _terminal(
            services,
            state,
            outcome=SolveOutcome.VERIFICATION_FAILED,
            summary="The candidate did not pass required deterministic verification.",
        )

    async def review_terminal(state: V2State) -> dict[str, AgentFinalOutput]:
        return _terminal(
            services,
            state,
            outcome=SolveOutcome.REVIEW_FAILED,
            summary="Independent semantic review did not accept the candidate.",
        )

    async def completed_terminal(state: V2State) -> dict[str, AgentFinalOutput]:
        diff = services.repository.get_complete_diff()
        digest = hashlib.sha256(diff.encode("utf-8")).hexdigest()
        if state["verification"].candidate_diff_digest != digest:
            raise AgentRuntimeError("Candidate changed after the reviewed verification pass.")
        uncertainties = [
            *state["plan"].non_blocking_uncertainties,
            *state["verification"].uncertainty,
            *state["review"].uncertainty,
        ]
        return _terminal(
            services,
            state,
            outcome=SolveOutcome.COMPLETED,
            summary=state["solver_result"].summary,
            remaining_uncertainty=uncertainties,
        )

    async def readiness_route(state: V2State):
        return await _readiness_route(
            state,
            max_context_expansions=settings.max_readiness_context_expansions,
        )

    async def solver_route(state: V2State):
        return await _solver_route(
            state,
            max_context_expansions=settings.max_solver_context_expansions,
        )

    async def candidate_route(state: V2State):
        return await _candidate_route(
            state,
            max_repairs=settings.max_implementation_repairs,
        )

    async def verification_route(state: V2State):
        return await _verification_route(
            state,
            max_repairs=settings.max_implementation_repairs,
        )

    async def implementation_repair_route(state: V2State):
        return await _implementation_repair_route(
            state,
            max_repairs=settings.max_implementation_repairs,
        )

    async def review_route(state: V2State):
        return await _review_route(
            state,
            max_repairs=settings.max_review_repairs,
        )

    async def review_repair_route(state: V2State):
        return await _review_repair_route(
            state,
            max_repairs=settings.max_review_repairs,
        )

    builder = StateGraph(
        V2State,
        input_schema=V2GraphInput,
        output_schema=V2GraphOutput,
    )
    nodes = {
        "preflight": preflight,
        "scout": scout,
        "compile_intake": compile_intake,
        "intake_planner": intake_planner,
        "readiness_guard": readiness_guard,
        "expand_readiness_context": expand_readiness_context,
        "compile_readiness_recheck": compile_readiness_recheck,
        "readiness_recheck": readiness_recheck,
        "readiness_recheck_guard": readiness_guard,
        "autonomy_commit": autonomy_commit,
        "compile_solver": compile_solver,
        "solver": solver,
        "solver_guard": readiness_guard,
        "expand_solver_context": expand_solver_context,
        "compile_solver_retry": compile_solver,
        "solver_retry": solver,
        "solver_retry_guard": readiness_guard,
        "apply_candidate": apply_candidate,
        "candidate_guard": readiness_guard,
        "hard_verify": hard_verify,
        "verification_guard": readiness_guard,
        "implementation_repair_gate": readiness_guard,
        "compile_implementation_repair": compile_implementation_repair,
        "implementation_repair": implementation_repair,
        "apply_implementation_repair": apply_candidate,
        "repaired_candidate_guard": readiness_guard,
        "hard_verify_after_repair": hard_verify,
        "verification_after_repair_guard": readiness_guard,
        "compile_review": compile_review,
        "reviewer": reviewer,
        "review_guard": readiness_guard,
        "review_repair_gate": readiness_guard,
        "compile_review_repair": compile_review_repair,
        "review_repair": review_repair,
        "apply_review_repair": apply_candidate,
        "review_repair_candidate_guard": readiness_guard,
        "hard_verify_after_review_repair": hard_verify,
        "review_repair_verification_guard": readiness_guard,
        "compile_rereview": compile_review,
        "rereviewer": reviewer,
        "rereview_guard": readiness_guard,
        "clarification_terminal": clarification_terminal,
        "blocked_terminal": blocked_terminal,
        "no_change_terminal": no_change_terminal,
        "human_after_start_terminal": human_after_start_terminal,
        "unresolved_terminal": unresolved_terminal,
        "verification_terminal": verification_terminal,
        "review_terminal": review_terminal,
        "completed_terminal": completed_terminal,
    }
    for name, node in nodes.items():
        builder.add_node(name, node)

    builder.add_edge(START, "preflight")
    builder.add_edge("preflight", "scout")
    builder.add_edge("scout", "compile_intake")
    builder.add_edge("compile_intake", "intake_planner")
    builder.add_edge("intake_planner", "readiness_guard")
    builder.add_conditional_edges(
        "readiness_guard",
        readiness_route,
        {
            "ready": "autonomy_commit",
            "repo_context": "expand_readiness_context",
            "clarification": "clarification_terminal",
            "blocked": "blocked_terminal",
        },
    )
    builder.add_edge("expand_readiness_context", "compile_readiness_recheck")
    builder.add_edge("compile_readiness_recheck", "readiness_recheck")
    builder.add_edge("readiness_recheck", "readiness_recheck_guard")
    builder.add_conditional_edges(
        "readiness_recheck_guard",
        _readiness_recheck_route,
        {
            "ready": "autonomy_commit",
            "clarification": "clarification_terminal",
            "blocked": "blocked_terminal",
        },
    )
    builder.add_edge("autonomy_commit", "compile_solver")
    builder.add_edge("compile_solver", "solver")
    builder.add_edge("solver", "solver_guard")
    builder.add_conditional_edges(
        "solver_guard",
        solver_route,
        {
            "implemented": "apply_candidate",
            "need_context": "expand_solver_context",
            "no_change": "no_change_terminal",
            "human": "human_after_start_terminal",
            "blocked": "unresolved_terminal",
        },
    )
    builder.add_edge("expand_solver_context", "compile_solver_retry")
    builder.add_edge("compile_solver_retry", "solver_retry")
    builder.add_edge("solver_retry", "solver_retry_guard")
    builder.add_conditional_edges(
        "solver_retry_guard",
        _solver_retry_route,
        {
            "implemented": "apply_candidate",
            "no_change": "no_change_terminal",
            "human": "human_after_start_terminal",
            "blocked": "unresolved_terminal",
        },
    )
    builder.add_edge("apply_candidate", "candidate_guard")
    builder.add_conditional_edges(
        "candidate_guard",
        candidate_route,
        {
            "pass": "hard_verify",
            "repair": "implementation_repair_gate",
            "fail": "unresolved_terminal",
        },
    )
    builder.add_edge("hard_verify", "verification_guard")
    builder.add_conditional_edges(
        "verification_guard",
        verification_route,
        {
            "pass": "compile_review",
            "repair": "implementation_repair_gate",
            "fail": "verification_terminal",
        },
    )
    builder.add_conditional_edges(
        "implementation_repair_gate",
        implementation_repair_route,
        {"allowed": "compile_implementation_repair", "denied": "unresolved_terminal"},
    )
    builder.add_edge("compile_implementation_repair", "implementation_repair")
    builder.add_edge("implementation_repair", "apply_implementation_repair")
    builder.add_edge("apply_implementation_repair", "repaired_candidate_guard")
    builder.add_conditional_edges(
        "repaired_candidate_guard",
        _repaired_candidate_route,
        {"pass": "hard_verify_after_repair", "fail": "unresolved_terminal"},
    )
    builder.add_edge("hard_verify_after_repair", "verification_after_repair_guard")
    builder.add_conditional_edges(
        "verification_after_repair_guard",
        _verification_after_repair_route,
        {"pass": "compile_review", "fail": "verification_terminal"},
    )
    builder.add_edge("compile_review", "reviewer")
    builder.add_edge("reviewer", "review_guard")
    builder.add_conditional_edges(
        "review_guard",
        review_route,
        {
            "pass": "completed_terminal",
            "repair": "review_repair_gate",
            "human": "human_after_start_terminal",
            "fail": "review_terminal",
        },
    )
    builder.add_conditional_edges(
        "review_repair_gate",
        review_repair_route,
        {"allowed": "compile_review_repair", "denied": "review_terminal"},
    )
    builder.add_edge("compile_review_repair", "review_repair")
    builder.add_edge("review_repair", "apply_review_repair")
    builder.add_edge("apply_review_repair", "review_repair_candidate_guard")
    builder.add_conditional_edges(
        "review_repair_candidate_guard",
        _repaired_candidate_route,
        {"pass": "hard_verify_after_review_repair", "fail": "review_terminal"},
    )
    builder.add_edge(
        "hard_verify_after_review_repair", "review_repair_verification_guard"
    )
    builder.add_conditional_edges(
        "review_repair_verification_guard",
        _verification_after_repair_route,
        {"pass": "compile_rereview", "fail": "verification_terminal"},
    )
    builder.add_edge("compile_rereview", "rereviewer")
    builder.add_edge("rereviewer", "rereview_guard")
    builder.add_conditional_edges(
        "rereview_guard",
        _rereview_route,
        {"pass": "completed_terminal", "fail": "review_terminal"},
    )
    for terminal in (
        "clarification_terminal",
        "blocked_terminal",
        "no_change_terminal",
        "human_after_start_terminal",
        "unresolved_terminal",
        "verification_terminal",
        "review_terminal",
        "completed_terminal",
    ):
        builder.add_edge(terminal, END)
    return builder.compile(name=GRAPH_NAME)


async def _readiness_route(
    state: V2State,
    *,
    max_context_expansions: int = 1,
) -> Literal["ready", "repo_context", "clarification", "blocked"]:
    disposition = state["intake"].disposition
    if disposition is ReadinessDisposition.READY_AUTONOMOUS:
        return "ready"
    if disposition is ReadinessDisposition.NEEDS_REPOSITORY_CONTEXT:
        return (
            "repo_context"
            if state.get("readiness_context_expansions", 0)
            < max_context_expansions
            else "blocked"
        )
    if disposition in {
        ReadinessDisposition.NEEDS_HUMAN_INFORMATION,
        ReadinessDisposition.NEEDS_HUMAN_DESIGN_DECISION,
    }:
        return "clarification"
    return "blocked"


async def _readiness_recheck_route(
    state: V2State,
) -> Literal["ready", "clarification", "blocked"]:
    route = await _readiness_route(state)
    return "blocked" if route == "repo_context" else route  # type: ignore[return-value]


async def _solver_route(
    state: V2State,
    *,
    max_context_expansions: int = 1,
) -> Literal["implemented", "need_context", "no_change", "human", "blocked"]:
    status = state["solver_result"].status
    if status is SolverStatus.IMPLEMENTED:
        return "implemented"
    if status is SolverStatus.NEED_CONTEXT:
        return (
            "need_context"
            if state.get("solver_context_expansions", 0) < max_context_expansions
            else "blocked"
        )
    if status is SolverStatus.NO_CHANGE:
        return "no_change"
    if status is SolverStatus.HUMAN_DECISION_DISCOVERED:
        return "human"
    return "blocked"


async def _solver_retry_route(
    state: V2State,
) -> Literal["implemented", "no_change", "human", "blocked"]:
    route = await _solver_route(state)
    return "blocked" if route == "need_context" else route  # type: ignore[return-value]


async def _candidate_route(
    state: V2State,
    *,
    max_repairs: int = 1,
) -> Literal["pass", "repair", "fail"]:
    if not state.get("candidate_error"):
        return "pass"
    if state.get("implementation_repairs", 0) < max_repairs:
        return "repair"
    return "fail"


async def _repaired_candidate_route(state: V2State) -> Literal["pass", "fail"]:
    return "fail" if state.get("candidate_error") else "pass"


async def _verification_route(
    state: V2State,
    *,
    max_repairs: int = 1,
) -> Literal["pass", "repair", "fail"]:
    if state["verification"].status is VerificationStatus.PASS:
        return "pass"
    if (
        not state.get("stuck")
        and state.get("implementation_repairs", 0) < max_repairs
    ):
        return "repair"
    return "fail"


async def _verification_after_repair_route(
    state: V2State,
) -> Literal["pass", "fail"]:
    return "pass" if state["verification"].status is VerificationStatus.PASS else "fail"


async def _implementation_repair_route(
    state: V2State,
    *,
    max_repairs: int = 1,
) -> Literal["allowed", "denied"]:
    return (
        "allowed"
        if state.get("implementation_repairs", 0) < max_repairs
        and not state.get("stuck", False)
        else "denied"
    )

async def _review_route(
    state: V2State,
    *,
    max_repairs: int = 1,
) -> Literal["pass", "repair", "human", "fail"]:
    review = state["review"]
    if review.verdict is ReviewVerdict.PASS:
        return "pass"
    if review.failure_type is ReviewFailureType.REQUIREMENT_AMBIGUITY:
        return "human"
    if (
        review.verdict in {ReviewVerdict.FAIL, ReviewVerdict.UNCERTAIN}
        and review.failure_type is ReviewFailureType.IMPLEMENTATION
        and review.blocking_findings
        and state.get("review_repairs", 0) < max_repairs
    ):
        return "repair"
    return "fail"


async def _review_repair_route(
    state: V2State,
    *,
    max_repairs: int = 1,
) -> Literal["allowed", "denied"]:
    return "allowed" if state.get("review_repairs", 0) < max_repairs else "denied"


async def _rereview_route(state: V2State) -> Literal["pass", "fail"]:
    return "pass" if state["review"].verdict is ReviewVerdict.PASS else "fail"


async def _invoke(
    services: V2Services,
    *,
    stage: str,
    role: ModelRole,
    instructions: str,
    context_ref: str,
    schema,
):
    content = Path(context_ref).read_text(encoding="utf-8")
    response = await services.calls.invoke(
        stage=stage,
        role=role,
        messages=[SystemMessage(content=instructions), HumanMessage(content=content)],
        schema=schema,
    )
    result = schema.model_validate(response.parsed)
    _log_structured_result(role, result)
    return result


def _log_structured_result(role: ModelRole, result: object) -> None:
    if isinstance(result, IntakeResult):
        details = (
            ("Decision", result.disposition.value),
            ("Plan tasks", len(result.plan.tasks) if result.plan is not None else 0),
            ("Questions", len(result.blocking_questions)),
            ("Context requests", len(result.retrieval_requests)),
        )
    elif isinstance(result, SolverResult):
        details = (
            ("Decision", result.status.value),
            ("Claimed files", len(result.changed_files_claimed)),
            ("Context requests", len(result.retrieval_requests)),
        )
    elif isinstance(result, ReviewResult):
        details = (
            ("Decision", result.verdict.value),
            ("Criteria checked", len(result.criterion_results)),
            ("Blocking findings", len(result.blocking_findings)),
            ("Confidence", f"{result.confidence:.2f}"),
        )
    else:
        details = (("Schema", type(result).__name__),)
    log_agent_result(logger, role=role, details=details)


def _persist_context(services: V2Services, packet: ContextPacket) -> str:
    next_call = len(services.calls.records) + 1
    return str(services.artifacts.write_context(packet.role, next_call, packet.content))


def _apply_solver_result(
    services: V2Services,
    result: SolverResult,
    contract: AutonomyContract,
) -> dict[str, object]:
    if result.status is not SolverStatus.IMPLEMENTED:
        return {"candidate_error": "Repair Solver did not return an implementation."}
    call_number = len(services.calls.records)
    try:
        patch = normalize_patch(result.patch)
        services.artifacts.write_proposal("solver", call_number, patch)
        services.repository.apply_patch(patch=patch)
        changed_files = services.repository.get_changed_files()
        diff = services.repository.get_complete_diff()
        if not diff.strip() or not changed_files:
            raise RepositoryError("Solver patch produced no authoritative Git diff.")
        violations = paths_outside_scopes(
            changed_files,
            allowed_scopes=contract.allowed_write_scopes,
            forbidden_scopes=contract.forbidden_write_scopes,
        )
        if violations:
            raise RepositoryError(
                "Candidate changed paths outside the frozen scope: "
                + ", ".join(violations[:10])
            )
    except RepositoryError as error:
        return {"candidate_error": str(error)}
    return {
        "candidate_error": "",
        "changed_files": changed_files,
        "candidate_diff_digest": hashlib.sha256(diff.encode("utf-8")).hexdigest(),
    }


def _first_failure_fingerprint(verification: VerificationResult) -> str:
    for check in verification.checks:
        if check.status in {
            VerificationStatus.FAIL,
            VerificationStatus.TIMEOUT,
        }:
            return check.fingerprint
    return ""


def _verification_reason(verification: VerificationResult) -> str:
    failures = [
        f"{check.check.check_id}: {check.status.value}: {check.output_excerpt}"
        for check in verification.checks
        if check.status in {VerificationStatus.FAIL, VerificationStatus.TIMEOUT}
    ]
    return "\n".join(failures)[:8_000] or "Verification did not pass."


def _terminal(
    services: V2Services,
    state: V2State,
    *,
    outcome: SolveOutcome,
    summary: str,
    clarification: ClarificationPacket | None = None,
    remaining_uncertainty: list[str] | tuple[str, ...] = (),
) -> dict[str, AgentFinalOutput]:
    final = AgentFinalOutput(
        summary=summary[:2_000] or outcome.value,
        changed_files_claimed=state.get("changed_files", []),
        remaining_uncertainty=list(remaining_uncertainty)[:10],
        outcome=outcome,
        clarification=clarification,
        provenance=services.calls.provenance(
            implementation_repairs=state.get("implementation_repairs", 0),
            review_repairs=state.get("review_repairs", 0),
            readiness_context_expansions=state.get("readiness_context_expansions", 0),
            solver_context_expansions=state.get("solver_context_expansions", 0),
        ),
    )
    services.artifacts.write_usage(final.provenance)
    services.artifacts.write_terminal(final)
    return {"final_output": final}


_CLARIFICATION_ROUND = re.compile(
    r"(?:<!--|&lt;!--)\s*sage-clarification:v1\s+round=(\d+)"
)


def _prior_clarification_round(issue_text: str) -> int:
    rounds = [int(value) for value in _CLARIFICATION_ROUND.findall(issue_text)]
    return max(rounds, default=0)
