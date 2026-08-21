"""Authorization and duplicate gate for GitHub Issue commands."""

from sage.errors import GitHubApiError, GitHubGateError
from sage.integrations.github.api_models import GitHubIssueCommentSnapshot
from sage.integrations.github.authorization import is_authorized_permission
from sage.integrations.github.branches import issue_branch_name
from sage.integrations.github.client import GitHubClient
from sage.integrations.github.gate_models import GateOutcome, GateResult
from sage.integrations.github.models import GitHubInvocation
from sage.integrations.github.status import (
    find_invocation_status,
    has_terminal_status,
    render_gate_status,
)


def evaluate_gate(
    invocation: GitHubInvocation,
    client: GitHubClient,
    *,
    max_comment_pages: int,
) -> GateResult:
    """Authorize one invocation and reject deterministic duplicate work."""

    if max_comment_pages < 1:
        raise ValueError("Maximum comment pages must be positive.")
    if invocation.command is None or invocation.issue.is_pull_request:
        return _result(invocation, GateOutcome.IGNORED)

    permission = client.get_repository_permission(
        invocation.repository,
        invocation.actor.login,
    )
    branch_name = issue_branch_name(invocation.issue.number)
    existing_pull_request_url: str | None = None

    if not is_authorized_permission(permission.permission):
        outcome = GateOutcome.UNAUTHORIZED
    else:
        pull_requests = client.list_open_pull_requests(
            invocation.repository,
            head_branch=branch_name,
            base_branch=invocation.default_branch,
        )
        if len(pull_requests) > 1:
            raise GitHubGateError(
                "GitHub returned multiple open Pull Requests for the Sage branch."
            )
        if pull_requests:
            outcome = GateOutcome.EXISTING_PULL_REQUEST
            existing_pull_request_url = pull_requests[0].html_url
        elif client.get_branch(invocation.repository, branch_name) is not None:
            outcome = GateOutcome.BLOCKED_EXISTING_BRANCH
        else:
            outcome = GateOutcome.ACCEPTED

    status_body = render_gate_status(
        invocation,
        outcome,
        branch_name=branch_name,
        existing_pull_request_url=existing_pull_request_url,
    )
    status_comment = _create_or_reuse_status(
        invocation,
        client,
        body=status_body,
        max_comment_pages=max_comment_pages,
    )
    return _result(
        invocation,
        outcome,
        status_comment_id=status_comment.comment_id,
        existing_pull_request_url=existing_pull_request_url,
    )


def _create_or_reuse_status(
    invocation: GitHubInvocation,
    client: GitHubClient,
    *,
    body: str,
    max_comment_pages: int,
) -> GitHubIssueCommentSnapshot:
    existing = find_invocation_status(
        invocation,
        client,
        max_comment_pages=max_comment_pages,
    )
    if existing is not None:
        if has_terminal_status(existing.body):
            return existing
        return client.update_issue_comment(
            invocation.repository,
            existing.comment_id,
            body,
        )

    try:
        return client.create_issue_comment(
            invocation.repository,
            invocation.issue.number,
            body,
        )
    except GitHubApiError as error:
        if not error.ambiguous:
            raise

    reconciled = find_invocation_status(
        invocation,
        client,
        max_comment_pages=max_comment_pages,
    )
    if reconciled is not None:
        return reconciled
    return client.create_issue_comment(
        invocation.repository,
        invocation.issue.number,
        body,
    )


def _result(
    invocation: GitHubInvocation,
    outcome: GateOutcome,
    *,
    status_comment_id: int | None = None,
    existing_pull_request_url: str | None = None,
) -> GateResult:
    return GateResult(
        outcome=outcome,
        should_run=outcome is GateOutcome.ACCEPTED,
        base_sha=invocation.base_sha,
        base_branch=invocation.default_branch,
        issue_number=invocation.issue.number,
        status_comment_id=status_comment_id,
        existing_pull_request_url=existing_pull_request_url,
    )
