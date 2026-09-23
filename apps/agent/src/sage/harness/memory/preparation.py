"""Prepare one accepted-base graph and its bounded initial context."""

from time import perf_counter

from sage.domain.memory import LegionMemoryRunArtifact, MemoryRetrievalBudgets, MemoryRetrievalStatus
from sage.domain.solve import PreparedRun, SolveRequest
from sage.errors import LegionMemoryBuildError, LegionMemoryError
from sage.harness.memory.service import LegionMemoryService
from sage.harness.memory.session import MemorySession, unavailable_memory_artifact


def prepare_memory(
    *,
    request: SolveRequest,
    prepared: PreparedRun,
    issue_text: str,
    service: LegionMemoryService | None,
    context_chars: int = 4000,
) -> tuple[MemorySession | None, LegionMemoryRunArtifact]:
    """Build and retrieve one base-SHA graph, or return a visible fallback."""

    assert request.memory_file is not None
    started = perf_counter()
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
            budgets=MemoryRetrievalBudgets(max_chars=context_chars),
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
            preflight_duration_ms=round((perf_counter() - started) * 1000, 2),
        )
        return session, session.artifact()
    except LegionMemoryError as error:
        return None, unavailable_memory_artifact(
            requested_memory_file=requested,
            resolved_memory_file=requested,
            failure_category=type(error).__name__,
        )
