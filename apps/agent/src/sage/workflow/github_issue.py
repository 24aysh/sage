"""Provider-specific orchestration around the existing issue solver."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from sage.config import Settings
from sage.domain.requests import SolveRequest
from sage.domain.results import SolveOutcome, SolveResult
from sage.domain.runtime import AgentRuntime
from sage.errors import (
    AgentRuntimeError,
    ArtifactError,
    ConfigurationError,
    GitHubContextError,
    GitHubIntegrationError,
    GitHubOrphanBranchError,
    GitHubPublicationError,
    RepositoryError,
    SandboxError,
    WorkspaceError,
)
from sage.integrations.github.authorization import is_authorized_permission
from sage.integrations.github.branches import issue_branch_name
from sage.integrations.github.client import GitHubClient
from sage.integrations.github.config import GitHubSettings
from sage.integrations.github.context import (
    build_issue_context,
    materialize_issue_context,
)
from sage.integrations.github.models import GitHubInvocation
from sage.integrations.github.provenance import (
    build_github_provenance,
    persist_github_diagnostics,
)
from sage.integrations.github.publishing import (
    PublicationOutcome,
    PublicationResult,
    publish_solve_result,
)
from sage.integrations.github.status import (
    WorkflowStatusState,
    finalize_invocation_status,
    find_invocation_status,
    has_terminal_status,
    transition_invocation_status,
)
from sage.repository.host_git import run_git
from sage.runtimes.factory import build_runtime
from sage.workflow.solve import solve_issue

logger = logging.getLogger(__name__)


class GitHubWorkflowOutcome(StrEnum):
    """Outcomes visible to the GitHub automation controller."""

    PULL_REQUEST_CREATED = "pull_request_created"
    NO_CHANGES = "no_changes"
    EXISTING_PULL_REQUEST = "existing_pull_request"
    UNAUTHORIZED = "unauthorized"
    BLOCKED_EXISTING_BRANCH = "blocked_existing_branch"
    HUMAN_REQUIRED_AFTER_START = "human_required_after_start"
    ENVIRONMENT_BLOCKED = "environment_blocked"
    UNRESOLVED = "unresolved"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    RATE_LIMITED = "rate_limited"
    BUDGET_EXHAUSTED = "budget_exhausted"
    INVALID_MODEL_OUTPUT = "invalid_model_output"
    VERIFICATION_FAILED = "verification_failed"
    REVIEW_FAILED = "review_failed"


class GitHubWorkflowResult(BaseModel):
    """Bounded workflow result without credentials or raw provider data."""

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    outcome: GitHubWorkflowOutcome
    solve_result: SolveResult | None = None
    publication: PublicationResult | None = None
    existing_pull_request_url: str | None = None


class Publisher(Protocol):
    """Injected deterministic publication boundary."""

    def __call__(
        self,
        invocation: GitHubInvocation,
        result: SolveResult,
        client: GitHubClient,
        *,
        github_token: str,
        runner_temp: Path,
        timeout_seconds: int,
    ) -> PublicationResult: ...


SettingsFactory = Callable[[], Settings]
RuntimeFactory = Callable[[Settings], AgentRuntime]
SolveRunner = Callable[
    [SolveRequest, AgentRuntime, Settings],
    Awaitable[SolveResult],
]


async def run_github_issue(
    invocation: GitHubInvocation,
    client: GitHubClient,
    github_settings: GitHubSettings,
    *,
    target_checkout: Path,
    context_dir: Path,
    diagnostics_dir: Path,
    runner_temp: Path,
    status_comment_id: int,
    settings_factory: SettingsFactory = Settings.from_env,
    runtime_factory: RuntimeFactory = build_runtime,
    solve_runner: SolveRunner = solve_issue,
    publisher: Publisher = publish_solve_result,
) -> GitHubWorkflowResult:
    """Reauthorize, solve at the event SHA, publish, and report one invocation."""

    branch_name = issue_branch_name(invocation.issue.number)
    solve_result: SolveResult | None = None
    try:
        expected = _repeat_solve_gate(invocation, client)
        if expected is not None:
            outcome, existing_url, category = expected
            transition_invocation_status(
                invocation,
                client,
                status_comment_id=status_comment_id,
                max_comment_pages=github_settings.max_comment_pages,
                state=WorkflowStatusState.FAILED,
                failure_category=category,
            )
            provenance = build_github_provenance(
                invocation,
                branch=branch_name,
                outcome=outcome.value,
                pull_request_url=existing_url,
            )
            persist_github_diagnostics(
                provenance,
                diagnostics_dir=diagnostics_dir,
            )
            return GitHubWorkflowResult(
                outcome=outcome,
                existing_pull_request_url=existing_url,
            )

        checkout = target_checkout.expanduser().resolve()
        _validate_exact_target_checkout(checkout, invocation.base_sha)
        _validate_runner_paths(
            checkout,
            context_dir=context_dir,
            diagnostics_dir=diagnostics_dir,
            runner_temp=runner_temp,
        )
        transition_invocation_status(
            invocation,
            client,
            status_comment_id=status_comment_id,
            max_comment_pages=github_settings.max_comment_pages,
            state=WorkflowStatusState.WORKING,
        )
        context = build_issue_context(
            invocation,
            client,
            max_comments=github_settings.max_comments,
            max_comment_pages=github_settings.max_comment_pages,
            max_context_chars=github_settings.max_context_chars,
        )
        issue_path = materialize_issue_context(
            context,
            context_dir=context_dir,
            target_checkout=checkout,
        )
        request = SolveRequest(
            repo_path=checkout,
            issue_path=issue_path,
            base_ref=invocation.base_sha,
        )
        settings = settings_factory()
        runtime = runtime_factory(settings)
        solve_result = await solve_runner(request, runtime, settings)
        if solve_result.base_sha != invocation.base_sha:
            raise GitHubPublicationError(
                "The solve result base does not match the validated event base."
            )
        terminal_mapping = _v2_terminal_mapping(solve_result.outcome)
        if terminal_mapping is not None:
            workflow_outcome, terminal_state = terminal_mapping
            provenance = build_github_provenance(
                invocation,
                branch=branch_name,
                outcome=workflow_outcome.value,
                current_base_sha=invocation.base_sha,
                local_run_id=solve_result.run_id,
            )
            persist_github_diagnostics(
                provenance,
                diagnostics_dir=diagnostics_dir,
                run_dir=solve_result.run_dir,
            )
            transition_invocation_status(
                invocation,
                client,
                status_comment_id=status_comment_id,
                max_comment_pages=github_settings.max_comment_pages,
                state=terminal_state,
                summary=solve_result.summary,
                remaining_uncertainty=solve_result.remaining_uncertainty,
            )
            return GitHubWorkflowResult(
                outcome=workflow_outcome,
                solve_result=solve_result,
            )
        if solve_result.outcome is SolveOutcome.NO_CHANGE or not solve_result.diff.strip():
            if solve_result.changed_files:
                raise GitHubPublicationError(
                    "The no-change solve result contains authoritative changed paths."
                )
            provenance = build_github_provenance(
                invocation,
                branch=branch_name,
                outcome=GitHubWorkflowOutcome.NO_CHANGES.value,
                current_base_sha=invocation.base_sha,
                local_run_id=solve_result.run_id,
            )
            persist_github_diagnostics(
                provenance,
                diagnostics_dir=diagnostics_dir,
                run_dir=solve_result.run_dir,
            )
            transition_invocation_status(
                invocation,
                client,
                status_comment_id=status_comment_id,
                max_comment_pages=github_settings.max_comment_pages,
                state=WorkflowStatusState.NO_CHANGES,
                summary=solve_result.summary,
                remaining_uncertainty=solve_result.remaining_uncertainty,
            )
            return GitHubWorkflowResult(
                outcome=GitHubWorkflowOutcome.NO_CHANGES,
                solve_result=solve_result,
            )
        if solve_result.outcome is not SolveOutcome.COMPLETED:
            raise GitHubPublicationError(
                "Only a completed solve outcome may reach publication."
            )
        if not solve_result.changed_files:
            raise GitHubPublicationError(
                "The non-empty solve diff has no authoritative changed paths."
            )

        publication = publisher(
            invocation,
            solve_result,
            client,
            github_token=github_settings.github_token,
            runner_temp=runner_temp,
            timeout_seconds=settings.command_timeout_seconds,
        )
        if publication.outcome is not PublicationOutcome.PULL_REQUEST_CREATED:
            raise GitHubPublicationError(
                "The publisher returned an invalid non-empty candidate outcome."
            )
        provenance = build_github_provenance(
            invocation,
            branch=publication.branch_name,
            outcome=GitHubWorkflowOutcome.PULL_REQUEST_CREATED.value,
            current_base_sha=publication.current_base_sha,
            local_run_id=solve_result.run_id,
            pull_request_number=publication.pull_request_number,
            pull_request_url=publication.pull_request_url,
        )
        persist_github_diagnostics(
            provenance,
            diagnostics_dir=diagnostics_dir,
            run_dir=solve_result.run_dir,
        )
        transition_invocation_status(
            invocation,
            client,
            status_comment_id=status_comment_id,
            max_comment_pages=github_settings.max_comment_pages,
            state=WorkflowStatusState.PULL_REQUEST_CREATED,
            summary=solve_result.summary,
            remaining_uncertainty=solve_result.remaining_uncertainty,
            pull_request_url=publication.pull_request_url,
        )
        return GitHubWorkflowResult(
            outcome=GitHubWorkflowOutcome.PULL_REQUEST_CREATED,
            solve_result=solve_result,
            publication=publication,
        )
    except Exception as error:
        category = classify_github_failure(error)
        branch_url = (
            error.branch_url
            if isinstance(error, GitHubOrphanBranchError)
            else None
        )
        try:
            provenance = build_github_provenance(
                invocation,
                branch=branch_name,
                outcome=f"failed:{category}",
                current_base_sha=(
                    invocation.base_sha if solve_result is not None else None
                ),
                local_run_id=(solve_result.run_id if solve_result is not None else None),
            )
            persist_github_diagnostics(
                provenance,
                diagnostics_dir=diagnostics_dir,
                run_dir=(solve_result.run_dir if solve_result is not None else None),
            )
        except Exception:
            logger.error("Unable to persist safe GitHub failure diagnostics.")
        try:
            transition_invocation_status(
                invocation,
                client,
                status_comment_id=status_comment_id,
                max_comment_pages=github_settings.max_comment_pages,
                state=WorkflowStatusState.FAILED,
                failure_category=category,
                branch_url=branch_url,
            )
        except Exception:
            logger.error("Unable to persist the safe GitHub terminal status.")
        raise


def finalize_github_issue(
    invocation: GitHubInvocation,
    client: GitHubClient,
    *,
    max_comment_pages: int,
) -> None:
    """Reconcile an interrupted non-terminal invocation after solve job exit."""

    if invocation.command is None or invocation.issue.is_pull_request:
        return
    existing_status = find_invocation_status(
        invocation,
        client,
        max_comment_pages=max_comment_pages,
    )
    if existing_status is None or has_terminal_status(existing_status.body):
        return
    branch_name = issue_branch_name(invocation.issue.number)
    pull_requests = client.list_open_pull_requests(
        invocation.repository,
        head_branch=branch_name,
        base_branch=invocation.default_branch,
    )
    if len(pull_requests) == 1:
        finalize_invocation_status(
            invocation,
            client,
            max_comment_pages=max_comment_pages,
            state=WorkflowStatusState.PULL_REQUEST_CREATED,
            pull_request_url=pull_requests[0].html_url,
        )
        return
    branch = client.get_branch(invocation.repository, branch_name)
    branch_url = (
        f"{invocation.repository.html_url}/tree/{branch_name}"
        if branch is not None
        else None
    )
    finalize_invocation_status(
        invocation,
        client,
        max_comment_pages=max_comment_pages,
        state=WorkflowStatusState.FAILED,
        failure_category=(
            "orphan_branch" if branch is not None else "job_interrupted"
        ),
        branch_url=branch_url,
    )


def classify_github_failure(error: Exception) -> str:
    """Map internal failures to concise non-secret Issue categories."""

    if isinstance(error, GitHubOrphanBranchError):
        return "pull_request_creation"
    if isinstance(error, GitHubPublicationError):
        return "publication"
    if isinstance(error, GitHubContextError):
        return "issue_context"
    if isinstance(error, AgentRuntimeError):
        return "agent_runtime"
    if isinstance(error, SandboxError):
        return "sandbox"
    if isinstance(error, ArtifactError):
        return "artifacts"
    if isinstance(error, (WorkspaceError, RepositoryError)):
        return "repository"
    if isinstance(error, ConfigurationError):
        return "configuration"
    if isinstance(error, GitHubIntegrationError):
        return "github_api_or_status"
    return "controller_failure"


def _v2_terminal_mapping(
    outcome: SolveOutcome,
) -> tuple[GitHubWorkflowOutcome, WorkflowStatusState] | None:
    mapping = {
        SolveOutcome.HUMAN_REQUIRED_AFTER_START: (
            GitHubWorkflowOutcome.HUMAN_REQUIRED_AFTER_START,
            WorkflowStatusState.HUMAN_REQUIRED_AFTER_START,
        ),
        SolveOutcome.ENVIRONMENT_BLOCKED: (
            GitHubWorkflowOutcome.ENVIRONMENT_BLOCKED,
            WorkflowStatusState.ENVIRONMENT_BLOCKED,
        ),
        SolveOutcome.UNRESOLVED: (
            GitHubWorkflowOutcome.UNRESOLVED,
            WorkflowStatusState.UNRESOLVED,
        ),
        SolveOutcome.BUDGET_EXHAUSTED: (
            GitHubWorkflowOutcome.BUDGET_EXHAUSTED,
            WorkflowStatusState.BUDGET_EXHAUSTED,
        ),
        SolveOutcome.INVALID_MODEL_OUTPUT: (
            GitHubWorkflowOutcome.INVALID_MODEL_OUTPUT,
            WorkflowStatusState.INVALID_MODEL_OUTPUT,
        ),
        SolveOutcome.PROVIDER_UNAVAILABLE: (
            GitHubWorkflowOutcome.PROVIDER_UNAVAILABLE,
            WorkflowStatusState.PROVIDER_UNAVAILABLE,
        ),
        SolveOutcome.RATE_LIMITED: (
            GitHubWorkflowOutcome.RATE_LIMITED,
            WorkflowStatusState.RATE_LIMITED,
        ),
        SolveOutcome.VERIFICATION_FAILED: (
            GitHubWorkflowOutcome.VERIFICATION_FAILED,
            WorkflowStatusState.VERIFICATION_FAILED,
        ),
        SolveOutcome.REVIEW_FAILED: (
            GitHubWorkflowOutcome.REVIEW_FAILED,
            WorkflowStatusState.REVIEW_FAILED,
        ),
    }
    return mapping.get(outcome)


def _repeat_solve_gate(
    invocation: GitHubInvocation,
    client: GitHubClient,
) -> tuple[GitHubWorkflowOutcome, str | None, str] | None:
    permission = client.get_repository_permission(
        invocation.repository,
        invocation.actor.login,
    )
    if not is_authorized_permission(permission.permission):
        return (
            GitHubWorkflowOutcome.UNAUTHORIZED,
            None,
            "authorization_changed",
        )
    branch_name = issue_branch_name(invocation.issue.number)
    pull_requests = client.list_open_pull_requests(
        invocation.repository,
        head_branch=branch_name,
        base_branch=invocation.default_branch,
    )
    if len(pull_requests) > 1:
        raise GitHubPublicationError(
            "GitHub returned multiple open Pull Requests for the Sage branch."
        )
    if pull_requests:
        return (
            GitHubWorkflowOutcome.EXISTING_PULL_REQUEST,
            pull_requests[0].html_url,
            "existing_pull_request",
        )
    if client.get_branch(invocation.repository, branch_name) is not None:
        return (
            GitHubWorkflowOutcome.BLOCKED_EXISTING_BRANCH,
            None,
            "existing_branch",
        )
    return None


def _validate_exact_target_checkout(checkout: Path, base_sha: str) -> None:
    if not checkout.is_dir():
        raise WorkspaceError("The exact GitHub target checkout is missing.")
    result = run_git(
        ["rev-parse", "--verify", "HEAD"],
        repository=checkout,
        timeout_seconds=30,
    )
    if result.returncode != 0 or result.stdout.strip() != base_sha:
        raise WorkspaceError(
            "The GitHub target checkout does not match the accepted exact SHA."
        )


def _validate_runner_paths(
    checkout: Path,
    *,
    context_dir: Path,
    diagnostics_dir: Path,
    runner_temp: Path,
) -> None:
    resolved = {
        "context": context_dir.expanduser().resolve(),
        "diagnostics": diagnostics_dir.expanduser().resolve(),
        "runner temporary": runner_temp.expanduser().resolve(),
    }
    for label, path in resolved.items():
        if path == checkout or checkout in path.parents:
            raise ConfigurationError(
                f"The {label} directory must be outside the target checkout."
            )
