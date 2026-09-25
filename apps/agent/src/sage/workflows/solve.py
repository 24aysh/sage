"""Resource lifecycle for one local Issue solve."""

from __future__ import annotations

import asyncio
import logging
from time import perf_counter
from collections.abc import Callable

from sage.artifacts.store import RunArtifacts
from sage.config import Settings
from sage.domain.retrieval import RetrievalRunArtifact
from sage.domain.solve import PreparedRun, SolveOutcome, SolveRequest, SolveResult
from sage.errors import ArtifactError, WorkspaceError
from sage.harness.retrieval.service import RepositoryRetrievalService
from sage.harness.retrieval.session import RetrievalSession
from sage.harness.retrieval.preparation import prepare_retrieval
from sage.observability import log_retrieval
from sage.harness.context.run import SolveContext, SolveEngine
from sage.harness.context.instructions import RoleInstructions
from sage.repository.service import Repository
from sage.repository.workspace import prepare_run
from sage.sandbox.base import Sandbox
from sage.sandbox.docker import DockerSandbox
from sage.verification.preflight import verification_environment_preflight

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
    retrieval_service: RepositoryRetrievalService | None = None,
    on_interrupted: Callable[[SolveResult], None] | None = None,
) -> SolveResult:
    """Execute one issue solve while guaranteeing sandbox cleanup."""
    workflow_started = perf_counter()
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

    retrieval_session: RetrievalSession | None = None
    retrieval_artifact: RetrievalRunArtifact | None = None
    sandbox: Sandbox | None = None
    try:
        instructions = RoleInstructions.load(prepared.workspace_dir, effective_settings)
        build_sandbox = sandbox_factory or _build_docker_sandbox
        sandbox = build_sandbox(prepared, effective_settings)
        sandbox.start()
        if effective_settings.verification_preflight:
            try:
                report = verification_environment_preflight(sandbox, effective_settings)
            except WorkspaceError as error:
                run_artifacts.write_verification_preflight(
                    {"status": "unavailable", "reason": str(error), "model_calls_started": False})
                raise
            run_artifacts.write_verification_preflight(report)
            logger.info("Verification environment preflight: ready (tooling only; tests not executed)")
        if request.index_file is not None:
            retrieval_session, retrieval_artifact = prepare_retrieval(
                request=request,
                prepared=prepared,
                issue_text=issue_text,
                service=retrieval_service,
                context_chars=(50_000 if effective_settings.jev.mode != "off"
                               else effective_settings.retrieval_initial_context_chars),
            )
            run_artifacts.write_retrieval(retrieval_artifact)
            if effective_settings.jev.mode == "off" or retrieval_session is None:
                log_retrieval(logger, retrieval_artifact)

        build_repository = repository_factory or _build_repository
        repository = build_repository(prepared, sandbox, effective_settings)
        context = SolveContext(
            prepared_run=prepared,
            repository=repository,
            settings=effective_settings,
            artifacts=run_artifacts,
            retrieval=retrieval_session,
            instructions=instructions,
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
            retrieval=(
                retrieval_session.artifact() if retrieval_session else retrieval_artifact
            ),
        )
        run_artifacts.write_result(final_output=final_output, result=result)
        logger.info("agent run completed", extra={"run_id": prepared.run_id})
    except (asyncio.CancelledError, KeyboardInterrupt):
        # Report before workflow cleanup; no Git inspection or model work after interruption.
        partial = SolveResult(run_id=prepared.run_id, base_sha=prepared.base_sha,
            summary="Interrupted; candidate changes have not been finalized or verified.",
            remaining_uncertainty=[], changed_files=[], diff="", run_dir=prepared.run_dir,
            workspace_dir=prepared.workspace_dir, outcome=SolveOutcome.INTERRUPTED,
            provenance=run_artifacts.latest_usage,
            retrieval=retrieval_session.artifact() if retrieval_session else retrieval_artifact,
            workflow_duration_ms=(perf_counter() - workflow_started) * 1000)
        try:
            run_artifacts.write_interrupted(partial)
        except ArtifactError:
            logger.warning("Unable to persist interruption summary; reporting available in-memory usage.")
        if on_interrupted is not None:
            on_interrupted(partial)
        raise
    finally:
        try:
            if sandbox is not None:
                sandbox.stop()
        finally:
            try:
                if retrieval_session is not None:
                    try:
                        run_artifacts.write_retrieval(retrieval_session.artifact())
                    finally:
                        retrieval_session.close()
            finally:
                duration_ms = (perf_counter() - workflow_started) * 1000
                run_artifacts.write_workflow_timing(duration_ms)
    return result.model_copy(update={"workflow_duration_ms": duration_ms})


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
