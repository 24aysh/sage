"""Resource lifecycle for one local Issue solve."""

from __future__ import annotations

import logging
from collections.abc import Callable

from sage.artifacts.store import RunArtifacts
from sage.config import Settings
from sage.domain.solve import PreparedRun, SolveOutcome, SolveRequest, SolveResult
from sage.errors import WorkspaceError
from sage.orchestration.context import SolveContext, SolveEngine
from sage.repository.service import Repository
from sage.repository.workspace import prepare_run
from sage.sandbox.base import Sandbox
from sage.sandbox.docker import DockerSandbox

logger = logging.getLogger(__name__)

SandboxFactory = Callable[[PreparedRun, Settings], Sandbox]
RepositoryFactory = Callable[[PreparedRun, Sandbox, Settings], Repository]


async def solve_issue(
    request: SolveRequest,
    orchestrator: SolveEngine,
    settings: Settings,
    *,
    sandbox_factory: SandboxFactory | None = None,
    repository_factory: RepositoryFactory | None = None,
    artifacts: RunArtifacts | None = None,
) -> SolveResult:
    """Execute one issue solve while guaranteeing sandbox cleanup."""

    effective_settings = settings
    if request.sandbox_image:
        effective_settings = settings.model_copy(
            update={"sandbox_image": request.sandbox_image}
        )

    issue_text = _read_issue(request)
    prepared = prepare_run(request, effective_settings)
    run_artifacts = artifacts or RunArtifacts(prepared.run_dir)
    run_artifacts.initialize(
        request=request,
        prepared_run=prepared,
        issue_text=issue_text,
        settings=effective_settings,
    )

    build_sandbox = sandbox_factory or _build_docker_sandbox
    sandbox = build_sandbox(prepared, effective_settings)
    try:
        sandbox.start()
        build_repository = repository_factory or _build_repository
        repository = build_repository(prepared, sandbox, effective_settings)
        context = SolveContext(
            prepared_run=prepared,
            repository=repository,
            settings=effective_settings,
            artifacts=run_artifacts,
        )
        final_output = await orchestrator.solve(issue_text=issue_text, context=context)
        diff = repository.get_complete_diff()
        changed_files = repository.get_changed_files()
        outcome = final_output.outcome
        if outcome is SolveOutcome.COMPLETED and (not diff.strip() or not changed_files):
            raise WorkspaceError(
                "Completed result does not contain an authoritative candidate."
            )
        if outcome is SolveOutcome.NO_CHANGE and (diff.strip() or changed_files):
            raise WorkspaceError("No-change result contains repository changes.")
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
            provenance=final_output.provenance,
        )
        run_artifacts.write_result(final_output=final_output, result=result)
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


def _build_repository(
    prepared: PreparedRun,
    sandbox: Sandbox,
    settings: Settings,
) -> Repository:
    return Repository(
        workspace_root=prepared.workspace_dir,
        sandbox=sandbox,
        settings=settings,
    )
