"""Tool-driven Solver role and plan-before-mutation policy."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Literal, Protocol, TypeVar

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel

from sage.agents.loop import build_graph as build_tool_graph
from sage.agents.loop import recursion_limit
from sage.agents.prompts import (
    SOLVER_INSTRUCTIONS,
    build_repair_message,
    build_solver_message,
)
from sage.agents.repository_tools import (
    RepositoryContext,
    build_repository_read_tools,
    build_show_diff_tool,
)
from sage.artifacts.store import RunArtifacts
from sage.config import Settings
from sage.domain.solve import PreparedRun
from sage.domain.solver import (
    SavedSolverPlan,
    SolverAcceptanceCriterion,
    SolverFinalResult,
    SolverPlan,
    SolverPlanTask,
)
from sage.domain.usage import AttemptKind, ModelRole
from sage.errors import AgentRuntimeError, RepositoryError
from sage.observability import agent_trace_config, log_agent_result
from sage.providers.calls import ModelCalls
from sage.research.service import ResearchService
from sage.research.tools import build_solver_research_tools
from sage.verification.discovery import is_allowed_solver_verification_command

logger = logging.getLogger(__name__)
OutputModel = TypeVar("OutputModel", bound=BaseModel)
SOLVE_GRAPH_NAME = "sage_v2_tool_driven"


class SolverContext(RepositoryContext, Protocol):
    """Run-scoped capabilities needed by the Solver role."""

    prepared_run: PreparedRun
    settings: Settings


class SolverAgent:
    """Run one fresh bounded Solver tool session."""

    def __init__(self, *, settings: Settings, model: BaseChatModel) -> None:
        self._settings = settings
        self._model = model

    async def run(
        self,
        *,
        stage: str,
        message: str,
        context: SolverContext,
        plans: SolverPlanSession,
        calls: ModelCalls,
        research: ResearchService,
    ) -> SolverFinalResult:
        input_cap = (
            self._settings.repair_input_chars
            if stage == "solver-repair"
            else self._settings.solver_input_chars
        )
        if len(message) > input_cap:
            raise AgentRuntimeError("Solver context exceeds the configured safe input cap.")
        parsed = await self._run_graph(
            stage=stage,
            message=message,
            context=context,
            calls=calls,
            tools=build_solver_tools(context, plans, research),
            output_schema=SolverFinalResult,
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

    async def _run_graph(
        self,
        *,
        stage: str,
        message: str,
        context: SolverContext,
        calls: ModelCalls,
        tools: Sequence[BaseTool],
        output_schema: type[OutputModel],
    ) -> OutputModel:
        calls.start_solver_session()
        model = self._model.bind_tools(
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
            max_turns=self._settings.max_turns,
            instructions=SOLVER_INSTRUCTIONS,
            output_schema=output_schema,
            graph_name=f"{SOLVE_GRAPH_NAME}_{stage.replace('-', '_')}",
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
                    model=self._settings.solver_model,
                    call_number=len(calls.records) + 1,
                ),
                "recursion_limit": recursion_limit(self._settings.max_turns),
            },
        )
        return output_schema.model_validate(result.get("final_output"))


class SolverPlanSession:
    """Run-scoped controller state backing save/revise plan tools."""

    def __init__(self, artifacts: RunArtifacts) -> None:
        self._artifacts = artifacts
        self._saved: SavedSolverPlan | None = None

    @property
    def saved(self) -> SavedSolverPlan | None:
        return self._saved

    def save(self, plan: SolverPlan) -> SavedSolverPlan:
        if self._saved is not None:
            raise RepositoryError("A Solver plan already exists; use revise_plan.")
        return self._persist(plan, revision_reason=None)

    def revise(
        self,
        *,
        prior_version: int,
        reason: str,
        plan: SolverPlan,
    ) -> SavedSolverPlan:
        if self._saved is None:
            raise RepositoryError("No Solver plan exists; use save_plan first.")
        if prior_version != self._saved.version:
            raise RepositoryError(
                f"Plan revision expected version {self._saved.version}, got "
                f"{prior_version}."
            )
        if not reason.strip():
            raise RepositoryError("Plan revision requires an evidence-based reason.")
        return self._persist(plan, revision_reason=reason.strip())

    def require_implementable(self) -> SavedSolverPlan:
        if self._saved is None:
            raise RepositoryError("Save an implementable Solver plan before mutation.")
        if self._saved.plan.status != "implementable":
            raise RepositoryError("A blocked Solver plan cannot authorize mutation.")
        return self._saved

    def _persist(
        self,
        plan: SolverPlan,
        *,
        revision_reason: str | None,
    ) -> SavedSolverPlan:
        version = 1 if self._saved is None else self._saved.version + 1
        saved = SavedSolverPlan(
            version=version,
            digest=plan.digest(),
            revision_reason=revision_reason,
            plan=plan,
        )
        self._artifacts.write_solver_plan(version, saved)
        self._saved = saved
        return saved


def build_solver_tools(
    context: SolverContext,
    plans: SolverPlanSession,
    research: ResearchService | None = None,
) -> list[BaseTool]:
    """Build the Solver's structured repository and research tool set."""

    @tool
    async def save_plan(
        issue_summary: str,
        approach: str,
        tasks: list[SolverPlanTask],
        acceptance_criteria: list[SolverAcceptanceCriterion],
        relevant_paths: list[str],
        verification_commands: list[str],
        assumptions: list[str],
        risks: list[str],
        status: Literal["implementable", "blocked"],
        research_result_ids: list[str] | None = None,
        blocker: str | None = None,
    ) -> str:
        """Validate and persist the complete plan before any file mutation."""

        saved = plans.save(
            SolverPlan(
                issue_summary=issue_summary,
                research_result_ids=tuple(research_result_ids or ()),
                approach=approach,
                tasks=tuple(tasks),
                acceptance_criteria=tuple(acceptance_criteria),
                relevant_paths=tuple(relevant_paths),
                verification_commands=tuple(verification_commands),
                assumptions=tuple(assumptions),
                risks=tuple(risks),
                status=status,
                blocker=blocker,
            )
        )
        return f"Saved Solver plan version {saved.version} ({saved.digest})."

    @tool
    async def revise_plan(
        prior_version: int,
        reason: str,
        issue_summary: str,
        approach: str,
        tasks: list[SolverPlanTask],
        acceptance_criteria: list[SolverAcceptanceCriterion],
        relevant_paths: list[str],
        verification_commands: list[str],
        assumptions: list[str],
        risks: list[str],
        status: Literal["implementable", "blocked"],
        research_result_ids: list[str] | None = None,
        blocker: str | None = None,
    ) -> str:
        """Persist a complete replacement for the current Solver plan."""

        saved = plans.revise(
            prior_version=prior_version,
            reason=reason,
            plan=SolverPlan(
                issue_summary=issue_summary,
                research_result_ids=tuple(research_result_ids or ()),
                approach=approach,
                tasks=tuple(tasks),
                acceptance_criteria=tuple(acceptance_criteria),
                relevant_paths=tuple(relevant_paths),
                verification_commands=tuple(verification_commands),
                assumptions=tuple(assumptions),
                risks=tuple(risks),
                status=status,
                blocker=blocker,
            ),
        )
        return f"Saved Solver plan version {saved.version} ({saved.digest})."

    @tool
    async def replace_text(
        path: str,
        old_text: str,
        new_text: str,
        expected_occurrences: int = 1,
    ) -> str:
        """Replace exact UTF-8 text after enforcing the saved-plan gate."""

        plans.require_implementable()
        return context.repository.replace_text(
            path=path,
            old_text=old_text,
            new_text=new_text,
            expected_occurrences=expected_occurrences,
        )

    @tool
    async def write_file(
        path: str,
        content: str,
        mode: Literal["create", "replace", "create_or_replace"],
    ) -> str:
        """Create or replace one UTF-8 file after enforcing the plan gate."""

        plans.require_implementable()
        return context.repository.write_file(
            path=path,
            content=content,
            mode=mode,
        )

    @tool
    async def delete_file(path: str) -> str:
        """Delete one regular file after enforcing the saved-plan gate."""

        plans.require_implementable()
        return context.repository.delete_file(path=path)

    @tool
    async def move_file(source_path: str, destination_path: str) -> str:
        """Move one file without overwrite after enforcing the plan gate."""

        plans.require_implementable()
        return context.repository.move_file(
            source_path=source_path,
            destination_path=destination_path,
        )

    @tool
    async def run_command(command: str, timeout_seconds: int | None = None) -> str:
        """Run a policy-checked repository command in the isolated sandbox."""

        plans.require_implementable()
        trusted_commands = {
            item.command for item in context.settings.verification_commands
        }
        if (
            command not in trusted_commands
            and not is_allowed_solver_verification_command(command)
        ):
            raise RepositoryError(
                "run_command accepts verification commands only; use structured "
                "file tools for mutations."
            )
        result = context.repository.run_command(
            command=command,
            timeout_seconds=timeout_seconds,
        )
        return context.repository.format_command_result(result)

    research_tools = (
        build_solver_research_tools(research)
        if research is not None
        else []
    )
    show_diff = build_show_diff_tool(
        context,
        description="Show actual bounded Git status, statistics, and candidate diff.",
    )
    return [
        *build_repository_read_tools(context),
        *research_tools,
        save_plan,
        revise_plan,
        replace_text,
        write_file,
        delete_file,
        move_file,
        show_diff,
        run_command,
    ]
