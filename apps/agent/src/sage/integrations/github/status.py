"""Deterministic, injection-free GitHub gate status comments."""

from html import escape

from sage.integrations.github.gate_models import GateOutcome
from sage.integrations.github.models import (
    GitHubInvocation,
    validate_branch_name,
    validate_github_url,
)

STATUS_BOT_LOGIN = "github-actions[bot]"
_INVOCATION_MARKER_PREFIX = "<!-- sage-invocation:"
_STATE_MARKER_PREFIX = "<!-- sage-state:"


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


def _code(value: str) -> str:
    neutralized = escape(value, quote=True).replace("@", "@\u200b")
    return f"<code>{neutralized}</code>"
