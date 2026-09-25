"""Prepare one accepted-base graph and its bounded initial context."""

from time import perf_counter

from sage.domain.retrieval import RetrievalRunArtifact, RetrievalBudgets, RetrievalStatus
from sage.domain.solve import PreparedRun, SolveRequest
from sage.errors import RetrievalBuildError, RetrievalError
from sage.harness.retrieval.service import RepositoryRetrievalService
from sage.harness.retrieval.session import RetrievalSession, unavailable_retrieval_artifact


def prepare_retrieval(
    *,
    request: SolveRequest,
    prepared: PreparedRun,
    issue_text: str,
    service: RepositoryRetrievalService | None,
    context_chars: int = 4000,
) -> tuple[RetrievalSession | None, RetrievalRunArtifact]:
    """Build and retrieve one base-SHA graph, or return a visible fallback."""

    assert request.index_file is not None
    started = perf_counter()
    requested = request.index_file.expanduser().resolve()
    if service is None:
        return None, unavailable_retrieval_artifact(
            requested_index_file=requested,
            resolved_index_file=requested,
            failure_category="RetrievalServiceUnavailable",
        )
    try:
        build = service.build_or_update_graph_tool(
            repo_root=prepared.workspace_dir,
            index_file=requested,
        )
        if build.indexed_sha != prepared.base_sha:
            raise RetrievalBuildError(
                "Repository retrieval index indexed SHA does not match the accepted base."
            )
        retrieval = service.retrieve_issue_context(
            issue_text=issue_text,
            repo_root=prepared.workspace_dir,
            index_file=build.index_file,
            budgets=RetrievalBudgets(max_chars=context_chars),
        )
        if retrieval.status is RetrievalStatus.UNAVAILABLE:
            return None, unavailable_retrieval_artifact(
                requested_index_file=requested,
                resolved_index_file=build.index_file,
                failure_category="RetrievalUnavailable",
                build=build,
                retrieval=retrieval,
            )
        if (
            retrieval.status
            not in {RetrievalStatus.USED, RetrievalStatus.NO_MATCH}
            or retrieval.indexed_sha != prepared.base_sha
            or retrieval.repository_id != build.repository_id
        ):
            raise RetrievalBuildError(
                "Repository retrieval index retrieval provenance does not match the accepted base."
            )
        session = RetrievalSession(
            service=service,
            repo_root=prepared.workspace_dir,
            requested_index_file=requested,
            index_file=build.index_file,
            build=build,
            retrieval=retrieval,
            preflight_duration_ms=round((perf_counter() - started) * 1000, 2),
        )
        return session, session.artifact()
    except RetrievalError as error:
        return None, unavailable_retrieval_artifact(
            requested_index_file=requested,
            resolved_index_file=requested,
            failure_category=type(error).__name__,
        )
