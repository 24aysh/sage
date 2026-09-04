"""Resource lifecycle for one local Issue solve."""

from __future__ import annotations

import logging
from collections.abc import Callable

from sage.artifacts.store import RunArtifacts
from sage.config import Settings
from sage.domain.memory import LegionMemoryRunArtifact, MemoryRetrievalStatus
from sage.domain.solve import PreparedRun, SolveOutcome, SolveRequest, SolveResult
from sage.errors import LegionMemoryBuildError, LegionMemoryError, WorkspaceError
from sage.legion_memory.service import LegionMemoryService
from sage.legion_memory.session import MemorySession, unavailable_memory_artifact
from sage.observability import log_legion_memory
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
    memory_service: LegionMemoryService | None = None,
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

    memory_session: MemorySession | None = None
    memory_artifact: LegionMemoryRunArtifact | None = None
    sandbox: Sandbox | None = None
    try:
        if request.memory_file is not None:
            memory_session, memory_artifact = _prepare_memory(
                request=request,
                prepared=prepared,
                issue_text=issue_text,
                service=memory_service,
            )
            run_artifacts.write_legion_memory(memory_artifact)
            log_legion_memory(logger, memory_artifact)

        build_sandbox = sandbox_factory or _build_docker_sandbox
        sandbox = build_sandbox(prepared, effective_settings)
        sandbox.start()
        build_repository = repository_factory or _build_repository
        repository = build_repository(prepared, sandbox, effective_settings)
        context = SolveContext(
            prepared_run=prepared,
            repository=repository,
            settings=effective_settings,
            artifacts=run_artifacts,
            memory=memory_session,
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
            memory=(
                memory_session.artifact() if memory_session else memory_artifact
            ),
        )
        run_artifacts.write_result(final_output=final_output, result=result)
        logger.info("agent run completed", extra={"run_id": prepared.run_id})
        return result
    finally:
        try:
            if sandbox is not None:
                sandbox.stop()
        finally:
            if memory_session is not None:
                try:
                    run_artifacts.write_legion_memory(memory_session.artifact())
                finally:
                    memory_session.close()


def _prepare_memory(
    *,
    request: SolveRequest,
    prepared: PreparedRun,
    issue_text: str,
    service: LegionMemoryService | None,
) -> tuple[MemorySession | None, LegionMemoryRunArtifact]:
    """Build and retrieve one base-SHA graph, or return a visible fallback."""

    assert request.memory_file is not None
    requested = request.memory_file.expanduser().resolve()
    if service is None:
        return None, unavailable_memory_artifact(
            requested_memory_file=requested,
            resolved_memory_file=requested,
            failure_category="MemoryServiceUnavailable",
        )
    try:
        build = service.build_or_update_graph_tool(
            repo_root=prepared.workspace_dir,
            memory_file=requested,
        )
        if build.indexed_sha != prepared.base_sha:
            raise LegionMemoryBuildError(
                "Legion Memory indexed SHA does not match the accepted base."
            )
        retrieval = service.retrieve_issue_context(
            issue_text=issue_text,
            repo_root=prepared.workspace_dir,
            memory_file=build.memory_file,
        )
        if retrieval.status is MemoryRetrievalStatus.UNAVAILABLE:
            return None, unavailable_memory_artifact(
                requested_memory_file=requested,
                resolved_memory_file=build.memory_file,
                failure_category="MemoryRetrievalUnavailable",
                build=build,
                retrieval=retrieval,
            )
        if (
            retrieval.status
            not in {MemoryRetrievalStatus.USED, MemoryRetrievalStatus.NO_MATCH}
            or retrieval.indexed_sha != prepared.base_sha
            or retrieval.repository_id != build.repository_id
        ):
            raise LegionMemoryBuildError(
                "Legion Memory retrieval provenance does not match the accepted base."
            )
        session = MemorySession(
            service=service,
            repo_root=prepared.workspace_dir,
            requested_memory_file=requested,
            memory_file=build.memory_file,
            build=build,
            retrieval=retrieval,
        )
        return session, session.artifact()
    except LegionMemoryError as error:
        return None, unavailable_memory_artifact(
            requested_memory_file=requested,
            resolved_memory_file=requested,
            failure_category=type(error).__name__,
        )


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
