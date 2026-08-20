"""Deterministic GitHub invocation status ownership and transitions."""

from __future__ import annotations

from html import escape
from enum import StrEnum
from typing import TYPE_CHECKING

from sage.errors import GitHubStatusError
from sage.integrations.github.api_models import GitHubIssueCommentSnapshot
from sage.integrations.github.gate_models import GateOutcome
from sage.integrations.github.models import (
    GitHubInvocation,
    validate_branch_name,
    validate_github_url,
)

if TYPE_CHECKING:
    from sage.integrations.github.client import GitHubClient

STATUS_BOT_LOGIN = "github-actions[bot]"
_INVOCATION_MARKER_PREFIX = "<!-- sage-invocation:"
_STATE_MARKER_PREFIX = "<!-- sage-state:"
_MAX_SUMMARY_CHARS = 1_000
_MAX_UNCERTAINTY_ITEMS = 5
_MAX_UNCERTAINTY_CHARS = 500


class WorkflowStatusState(StrEnum):
    """Machine-readable states for one accepted solve invocation."""

    ACCEPTED = "accepted"
    WORKING = "working"
    PULL_REQUEST_CREATED = "pull_request_created"
    NO_CHANGES = "no_changes"
    FAILED = "failed"


TERMINAL_STATUS_STATES = frozenset(
    {
        WorkflowStatusState.PULL_REQUEST_CREATED,
        WorkflowStatusState.NO_CHANGES,
        WorkflowStatusState.FAILED,
    }
)

_ALLOWED_TRANSITIONS = {
    WorkflowStatusState.ACCEPTED: frozenset(
        {WorkflowStatusState.WORKING, WorkflowStatusState.FAILED}
    ),
    WorkflowStatusState.WORKING: TERMINAL_STATUS_STATES,
}


def invocation_marker(comment_id: int) -> str:
    """Return the stable hidden marker for an invoking Issue comment."""

    if comment_id < 1:
        raise ValueError("Comment ID must be positive.")
    return f"{_INVOCATION_MARKER_PREFIX}{comment_id} -->"


def has_invocation_marker(body: str, comment_id: int) -> bool:
    """Return whether a status body belongs to the given invocation."""

    return invocation_marker(comment_id) in body


def has_sage_status_marker(body: str) -> bool:
    """Return whether a body contains a project-owned status marker."""

    return _INVOCATION_MARKER_PREFIX in body


def status_state(body: str) -> WorkflowStatusState | None:
    """Return the one trusted state marker in a status body, if present."""

    matches = [
        state
        for state in WorkflowStatusState
        if f"{_STATE_MARKER_PREFIX}{state.value} -->" in body
    ]
    return matches[0] if len(matches) == 1 else None


def has_terminal_status(body: str) -> bool:
    """Return whether a status has one recognized terminal state marker."""

    return status_state(body) in TERMINAL_STATUS_STATES


def find_invocation_status(
    invocation: GitHubInvocation,
    client: GitHubClient,
    *,
    max_comment_pages: int,
) -> GitHubIssueCommentSnapshot | None:
    """Find the newest bot-owned status for one invocation marker."""

    if max_comment_pages < 1:
        raise ValueError("Maximum comment pages must be positive.")
    first_page = client.list_issue_comments(
        invocation.repository,
        invocation.issue.number,
        page=1,
        per_page=100,
    )
    oldest_page = max(1, first_page.last_page - max_comment_pages + 1)
    for page_number in range(first_page.last_page, oldest_page - 1, -1):
        page = (
            first_page
            if page_number == 1
            else client.list_issue_comments(
                invocation.repository,
                invocation.issue.number,
                page=page_number,
                per_page=100,
            )
        )
        for comment in reversed(page.comments):
            if comment.author_login == STATUS_BOT_LOGIN and has_invocation_marker(
                comment.body,
                invocation.comment.comment_id,
            ):
                return comment
    return None


def render_gate_status(
    invocation: GitHubInvocation,
    outcome: GateOutcome,
    *,
    branch_name: str,
    existing_pull_request_url: str | None = None,
) -> str:
    """Render a bounded gate status without including untrusted Issue text."""

    branch_name = validate_branch_name(branch_name)
    if existing_pull_request_url is not None:
        existing_pull_request_url = validate_github_url(existing_pull_request_url)
    marker = invocation_marker(invocation.comment.comment_id)
    state_marker = f"{_STATE_MARKER_PREFIX}{outcome.value} -->"
    run_link = f"[View the Actions run]({invocation.actions_run.html_url})."

    if outcome is GateOutcome.ACCEPTED:
        message = (
            "### Sage: request accepted\n\n"
            f"Authorized command from {_code(invocation.actor.login)} for Issue "
            f"{_code(f'#{invocation.issue.number}')}. Sage queued an isolated "
            f"solve against {_code(invocation.base_sha[:12])} on "
            f"{_code(invocation.default_branch)}."
        )
    elif outcome is GateOutcome.UNAUTHORIZED:
        message = (
            "### Sage: command not authorized\n\n"
            "This command requires current repository `write` or `admin` "
            "permission. No solve was started."
        )
    elif outcome is GateOutcome.EXISTING_PULL_REQUEST:
        if existing_pull_request_url is None:
            raise ValueError("Existing Pull Request status requires its URL.")
        message = (
            "### Sage: existing work found\n\n"
            "Sage did not start another solve because "
            f"[an open Pull Request]({existing_pull_request_url}) already uses "
            f"{_code(branch_name)}."
        )
    elif outcome is GateOutcome.BLOCKED_EXISTING_BRANCH:
        message = (
            "### Sage: existing branch needs attention\n\n"
            f"Sage will not overwrite {_code(branch_name)}. Create or link its Pull "
            "Request, or deliberately delete the stale remote branch before "
            "trying again. No solve was started."
        )
    else:
        raise ValueError("Ignored invocations do not receive status comments.")

    return f"{marker}\n{state_marker}\n{message}\n\n{run_link}"


def render_workflow_status(
    invocation: GitHubInvocation,
    state: WorkflowStatusState,
    *,
    summary: str = "",
    remaining_uncertainty: list[str] | tuple[str, ...] = (),
    pull_request_url: str | None = None,
    failure_category: str | None = None,
    branch_url: str | None = None,
) -> str:
    """Render a bounded working or terminal status body."""

    if state is WorkflowStatusState.ACCEPTED:
        raise ValueError("Accepted status is rendered by the gate.")
    if pull_request_url is not None:
        pull_request_url = validate_github_url(pull_request_url)
    if branch_url is not None:
        branch_url = validate_github_url(branch_url)

    if state is WorkflowStatusState.WORKING:
        message = (
            "### Sage: solve in progress\n\n"
            f"Sage started an isolated solve for Issue {_code(f'#{invocation.issue.number}')} "
            f"against {_code(invocation.base_sha[:12])}."
        )
    elif state is WorkflowStatusState.PULL_REQUEST_CREATED:
        if pull_request_url is None:
            raise ValueError("Pull Request completion requires its URL.")
        message = (
            "### Sage: draft Pull Request created\n\n"
            f"[Review the draft Pull Request]({pull_request_url})."
            f"{_summary_section(summary)}"
            f"{_uncertainty_section(remaining_uncertainty)}"
        )
    elif state is WorkflowStatusState.NO_CHANGES:
        message = (
            "### Sage: completed without repository changes\n\n"
            "The isolated solve produced no Git diff, so Sage did not create a "
            "branch or Pull Request."
            f"{_summary_section(summary)}"
            f"{_uncertainty_section(remaining_uncertainty)}"
        )
    elif state is WorkflowStatusState.FAILED:
        category = _safe_markdown(failure_category or "controller_failure", 100)
        message = (
            "### Sage: solve failed safely\n\n"
            f"Category: `{category}`. Sage did not overwrite an existing branch "
            "or push the default branch. Inspect the linked run, correct the "
            "reported boundary, and then create one new exact command comment."
        )
        if branch_url is not None:
            message += (
                "\n\nA candidate branch was preserved after publication failed: "
                f"[inspect the branch]({branch_url}). Create its Pull Request "
                "manually or delete it deliberately before retrying."
            )
    else:
        raise ValueError("Unsupported workflow status state.")

    return (
        f"{invocation_marker(invocation.comment.comment_id)}\n"
        f"{_STATE_MARKER_PREFIX}{state.value} -->\n"
        f"{message}\n\n[View the Actions run]({invocation.actions_run.html_url})."
    )


def transition_invocation_status(
    invocation: GitHubInvocation,
    client: GitHubClient,
    *,
    status_comment_id: int,
    max_comment_pages: int,
    state: WorkflowStatusState,
    summary: str = "",
    remaining_uncertainty: list[str] | tuple[str, ...] = (),
    pull_request_url: str | None = None,
    failure_category: str | None = None,
    branch_url: str | None = None,
) -> GitHubIssueCommentSnapshot:
    """Validate ownership and transition the invocation's single status."""

    existing = find_invocation_status(
        invocation,
        client,
        max_comment_pages=max_comment_pages,
    )
    if existing is None or existing.comment_id != status_comment_id:
        raise GitHubStatusError(
            "The trusted GitHub invocation status comment could not be found."
        )
    current = status_state(existing.body)
    if current is None:
        raise GitHubStatusError(
            "The GitHub invocation status has an invalid state marker."
        )
    if current in TERMINAL_STATUS_STATES:
        if current is state:
            return existing
        raise GitHubStatusError(
            "A terminal GitHub invocation status cannot be overwritten."
        )
    if state not in _ALLOWED_TRANSITIONS.get(current, frozenset()):
        raise GitHubStatusError(
            f"Invalid GitHub invocation status transition: {current.value} to "
            f"{state.value}."
        )
    body = render_workflow_status(
        invocation,
        state,
        summary=summary,
        remaining_uncertainty=remaining_uncertainty,
        pull_request_url=pull_request_url,
        failure_category=failure_category,
        branch_url=branch_url,
    )
    return client.update_issue_comment(
        invocation.repository,
        status_comment_id,
        body,
    )


def finalize_invocation_status(
    invocation: GitHubInvocation,
    client: GitHubClient,
    *,
    max_comment_pages: int,
    state: WorkflowStatusState,
    pull_request_url: str | None = None,
    failure_category: str | None = None,
    branch_url: str | None = None,
) -> GitHubIssueCommentSnapshot | None:
    """Repair one non-terminal status without overwriting terminal detail."""

    if state not in TERMINAL_STATUS_STATES:
        raise ValueError("Finalizer may write only terminal states.")
    existing = find_invocation_status(
        invocation,
        client,
        max_comment_pages=max_comment_pages,
    )
    if existing is None or has_terminal_status(existing.body):
        return existing
    if status_state(existing.body) is None:
        raise GitHubStatusError(
            "The GitHub invocation status has an invalid state marker."
        )
    body = render_workflow_status(
        invocation,
        state,
        pull_request_url=pull_request_url,
        failure_category=failure_category,
        branch_url=branch_url,
    )
    return client.update_issue_comment(
        invocation.repository,
        existing.comment_id,
        body,
    )


def _code(value: str) -> str:
    neutralized = escape(value, quote=True).replace("@", "@\u200b")
    return f"<code>{neutralized}</code>"


def _summary_section(summary: str) -> str:
    safe = _safe_markdown(summary, _MAX_SUMMARY_CHARS)
    return f"\n\n**Summary**\n\n{safe}" if safe else ""


def _uncertainty_section(values: list[str] | tuple[str, ...]) -> str:
    safe_values = [
        _safe_markdown(value, _MAX_UNCERTAINTY_CHARS)
        for value in values[:_MAX_UNCERTAINTY_ITEMS]
    ]
    safe_values = [value for value in safe_values if value]
    if not safe_values:
        return ""
    return "\n\n**Remaining uncertainty**\n\n" + "\n".join(
        f"- {value}" for value in safe_values
    )


def _safe_markdown(value: str, limit: int) -> str:
    normalized = "".join(
        character
        if character in "\n\t" or 32 <= ord(character) != 127
        else "�"
        for character in value
    )
    normalized = normalized.replace("<!-- sage-", "&lt;!-- sage-")
    normalized = normalized.replace("@", "@\u200b").strip()
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: max(0, limit - 18)].rstrip()}… [truncated]"
