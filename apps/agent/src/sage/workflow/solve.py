"""V2 issue-solving workflow orchestration."""

from __future__ import annotations

import logging
from collections.abc import Callable

from sage.artifacts import ArtifactStore
from sage.config import Settings
from sage.domain.requests import PreparedRun, SolveRequest
from sage.domain.results import SolveOutcome, SolveResult
from sage.domain.runtime import AgentRuntime, RuntimeContext
from sage.errors import WorkspaceError
from sage.repository import RepositoryTools
from sage.repository.workspace import prepare_run
from sage.sandbox.base import Sandbox
from sage.sandbox.docker import DockerSandbox

logger = logging.getLogger(__name__)

SandboxFactory = Callable[[PreparedRun, Settings], Sandbox]
RepositoryFactory = Callable[[PreparedRun, Sandbox, Settings], RepositoryTools]


async def solve_issue(
    request: SolveRequest,
    runtime: AgentRuntime,
    settings: Settings,
    *,
    sandbox_factory: SandboxFactory | None = None,
    repository_factory: RepositoryFactory | None = None,
    artifact_store: ArtifactStore | None = None,
) -> SolveResult:
    """Execute one issue solve while guaranteeing sandbox cleanup."""

    effective_settings = settings
    if request.sandbox_image:
        effective_settings = settings.model_copy(
            update={"sandbox_image": request.sandbox_image}
        )

    issue_text = _read_issue(request)
    prepared = prepare_run(request, effective_settings)
    store = artifact_store or ArtifactStore()
    store.initialize_run(
        request=request,
        prepared_run=prepared,
        issue_text=issue_text,
        settings=effective_settings,
    )

    build_sandbox = sandbox_factory or _build_docker_sandbox
    sandbox = build_sandbox(prepared, effective_settings)
    try:
        sandbox.start()
        build_repository = repository_factory or _build_repository_tools
        repository = build_repository(prepared, sandbox, effective_settings)
        context = RuntimeContext(
            prepared_run=prepared,
            sandbox=sandbox,
            repository=repository,
            settings=effective_settings,
        )
        final_output = await runtime.solve(issue_text=issue_text, context=context)
        diff = repository.get_complete_diff()
        changed_files = repository.get_changed_files()
        outcome = final_output.outcome
        if outcome is SolveOutcome.COMPLETED and (not diff.strip() or not changed_files):
            raise WorkspaceError(
                "Completed V2 result does not contain an authoritative candidate."
            )
        if outcome is SolveOutcome.NO_CHANGE and (diff.strip() or changed_files):
            raise WorkspaceError("V2 no-change result contains repository changes.")
        pre_mutation_outcomes = {
            SolveOutcome.NEEDS_HUMAN_INFORMATION,
            SolveOutcome.NEEDS_HUMAN_DESIGN_DECISION,
            SolveOutcome.NEEDS_MAINTAINER_REWRITE,
            SolveOutcome.HUMAN_REQUIRED,
            SolveOutcome.ENVIRONMENT_BLOCKED,
            SolveOutcome.UNSUPPORTED,
        }
        if outcome in pre_mutation_outcomes and (diff.strip() or changed_files):
            raise WorkspaceError(
                "A pre-mutation V2 outcome contains repository changes."
            )
        result = SolveResult(
            run_id=prepared.run_id,
            base_sha=prepared.base_sha,
            summary=final_output.summary,
            remaining_uncertainty=final_output.remaining_uncertainty,
            changed_files=changed_files,
            diff=diff,
            run_dir=prepared.run_dir,
            workspace_dir=prepared.workspace_dir,
            outcome=outcome,
            clarification=final_output.clarification,
            provenance=final_output.provenance,
        )
        store.persist_result(final_output=final_output, result=result)
        logger.info("agent run completed", extra={"run_id": prepared.run_id})
        return result
    finally:
        sandbox.stop()


def _read_issue(request: SolveRequest) -> str:
    try:
        return request.issue_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise WorkspaceError(f"Unable to read issue file: {request.issue_path}") from error


def _build_docker_sandbox(prepared: PreparedRun, settings: Settings) -> Sandbox:
    return DockerSandbox(prepared_run=prepared, settings=settings)


def _build_repository_tools(
    prepared: PreparedRun,
    sandbox: Sandbox,
    settings: Settings,
) -> RepositoryTools:
    return RepositoryTools(
        workspace_root=prepared.workspace_dir,
        sandbox=sandbox,
        settings=settings,
    )
