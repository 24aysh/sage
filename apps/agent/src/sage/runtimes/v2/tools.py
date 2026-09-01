"""Role-specific tools and plan-before-mutation policy for Sage V2."""

from __future__ import annotations

from typing import Literal

from langchain_core.tools import BaseTool, tool

from sage.artifacts.v2 import V2ArtifactStore
from sage.domain.runtime import RuntimeContext
from sage.domain.solver import (
    SavedSolverPlan,
    SolverAcceptanceCriterion,
    SolverPlan,
    SolverPlanTask,
)
from sage.errors import RepositoryError
from sage.memory.models import MutationAuthorization
from sage.research.models import ResearchRole
from sage.research.service import ResearchService
from sage.research.tools import build_research_tools
from sage.repository.edits import WriteMode
from sage.runtimes.repository_tools import (
    build_repository_read_tools,
    build_show_diff_tool,
)
from sage.verification.discovery import is_allowed_solver_verification_command


class SolverPlanSession:
    """Run-scoped controller state backing save/revise plan tools."""

    def __init__(self, artifacts: V2ArtifactStore) -> None:
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
    context: RuntimeContext,
    plans: SolverPlanSession,
    research: ResearchService | None = None,
) -> list[BaseTool]:
    """Build the V2 Solver's structured repository tool set."""

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

        validated_research_ids = _validate_research_result_ids(
            research_result_ids or (), research
        )
        saved = plans.save(
            SolverPlan(
                issue_summary=issue_summary,
                research_result_ids=validated_research_ids,
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

        validated_research_ids = _validate_research_result_ids(
            research_result_ids or (), research
        )
        saved = plans.revise(
            prior_version=prior_version,
            reason=reason,
            plan=SolverPlan(
                issue_summary=issue_summary,
                research_result_ids=validated_research_ids,
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
        if context.memory_session is not None:
            context.memory_session.authorize_mutation(
                MutationAuthorization(
                    operation="replace",
                    path=path,
                    old_text=old_text,
                )
            )
        result = context.repository.replace_text(
            path=path,
            old_text=old_text,
            new_text=new_text,
            expected_occurrences=expected_occurrences,
        )
        if context.memory_session is not None:
            context.memory_session.record_mutation(path)
        return result

    @tool
    async def write_file(
        path: str,
        content: str,
        mode: Literal["create", "replace", "create_or_replace"],
    ) -> str:
        """Create or replace one UTF-8 file after enforcing the plan gate."""

        plans.require_implementable()
        if context.memory_session is not None:
            context.memory_session.authorize_mutation(
                MutationAuthorization(
                    operation="write",
                    path=path,
                    replacing_entire_file=mode != "create",
                )
            )
        result = context.repository.write_file(
            path=path,
            content=content,
            mode=WriteMode(mode),
        )
        if context.memory_session is not None:
            context.memory_session.record_mutation(path)
        return result

    @tool
    async def delete_file(path: str) -> str:
        """Delete one regular file after enforcing the saved-plan gate."""

        plans.require_implementable()
        if context.memory_session is not None:
            context.memory_session.authorize_mutation(
                MutationAuthorization(operation="delete", path=path)
            )
        result = context.repository.delete_file(path=path)
        if context.memory_session is not None:
            context.memory_session.record_mutation(path)
        return result

    @tool
    async def move_file(source_path: str, destination_path: str) -> str:
        """Move one file without overwrite after enforcing the plan gate."""

        plans.require_implementable()
        if context.memory_session is not None:
            context.memory_session.authorize_mutation(
                MutationAuthorization(
                    operation="move",
                    path=source_path,
                    destination_path=destination_path,
                )
            )
        result = context.repository.move_file(
            source_path=source_path,
            destination_path=destination_path,
        )
        if context.memory_session is not None:
            context.memory_session.record_mutation(source_path, destination_path)
        return result

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
                "V2 run_command accepts verification commands only; use structured "
                "file tools for mutations."
            )
        result = context.repository.run_command(
            command=command,
            timeout_seconds=timeout_seconds,
        )
        if context.memory_session is not None:
            context.memory_session.record_mutation(
                *context.repository.get_changed_files()
            )
        return context.repository.format_command_result(result)

    research_tools = (
        build_research_tools(research, role=ResearchRole.SOLVER, allow_web=True)
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


def _validate_research_result_ids(
    result_ids: list[str] | tuple[str, ...],
    research: ResearchService | None,
) -> tuple[str, ...]:
    normalized = tuple(result_ids)
    unknown = (
        normalized
        if research is None
        else research.unknown_result_ids(normalized)
    )
    if unknown:
        raise RepositoryError(
            "research_result_ids accepts only IDs returned by same-run research "
            "tools; repository paths and memory paths belong in relevant_paths."
        )
    return normalized
