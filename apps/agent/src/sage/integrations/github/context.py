"""Collection and materialization of bounded GitHub Issue context."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sage.artifacts.files import write_text_atomic
from sage.errors import GitHubContextError
from sage.integrations.github.api_models import (
    GitHubIssueCommentSnapshot,
    GitHubIssueSnapshot,
)
from sage.integrations.github.client import GitHubClient
from sage.integrations.github.commands import parse_command
from sage.integrations.github.config import (
    MAX_COMMENT_PAGES,
    MAX_CONTEXT_CHARS,
    MAX_CONTEXT_COMMENTS,
    MIN_CONTEXT_CHARS,
)
from sage.integrations.github.context_models import GitHubIssueContext
from sage.integrations.github.context_rendering import render_context_document
from sage.integrations.github.models import GitHubInvocation
from sage.integrations.github.status import (
    STATUS_BOT_LOGIN,
    has_sage_clarification_marker,
    has_sage_status_marker,
)

__all__ = [
    "GitHubIssueContext",
    "build_issue_context",
    "materialize_issue_context",
    "render_issue_context",
]


@dataclass(frozen=True, slots=True)
class _CommentCollection:
    comments: tuple[GitHubIssueCommentSnapshot, ...]
    truncated: bool


def build_issue_context(
    invocation: GitHubInvocation,
    client: GitHubClient,
    *,
    max_comments: int,
    max_comment_pages: int,
    max_context_chars: int,
) -> GitHubIssueContext:
    """Fetch current Issue data and render one deterministic task document."""

    _validate_limits(
        max_comments=max_comments,
        max_comment_pages=max_comment_pages,
        max_context_chars=max_context_chars,
    )
    issue = client.get_issue(invocation.repository, invocation.issue.number)
    _validate_current_issue(invocation, issue)
    collection = _collect_comments(
        invocation,
        client,
        max_comments=max_comments,
        max_comment_pages=max_comment_pages,
    )
    return render_issue_context(
        invocation,
        issue,
        collection.comments,
        history_truncated=collection.truncated,
        max_context_chars=max_context_chars,
    )


def render_issue_context(
    invocation: GitHubInvocation,
    issue: GitHubIssueSnapshot,
    comments: tuple[GitHubIssueCommentSnapshot, ...],
    *,
    history_truncated: bool,
    max_context_chars: int,
) -> GitHubIssueContext:
    """Validate relationships and render context without external calls."""

    _validate_current_issue(invocation, issue)
    eligible_comments = tuple(
        comment
        for comment in comments
        if _is_eligible_comment(invocation, comment)
    )
    return render_context_document(
        invocation,
        issue,
        eligible_comments,
        history_truncated=history_truncated,
        max_context_chars=max_context_chars,
    )


def materialize_issue_context(
    context: GitHubIssueContext,
    *,
    context_dir: Path,
    target_checkout: Path,
) -> Path:
    """Atomically create the Issue file outside the target checkout."""

    resolved_directory = context_dir.expanduser().resolve()
    resolved_checkout = target_checkout.expanduser().resolve()
    if (
        resolved_directory == resolved_checkout
        or resolved_checkout in resolved_directory.parents
    ):
        raise GitHubContextError(
            "GitHub Issue context must be stored outside the target checkout."
        )

    context_path = resolved_directory / (
        f"sage-issue-{context.issue_number}-"
        f"comment-{context.invocation_comment_id}.md"
    )
    try:
        resolved_directory.mkdir(parents=True, exist_ok=True)
        write_text_atomic(context_path, context.markdown)
    except OSError as error:
        raise GitHubContextError(
            "Unable to materialize bounded GitHub Issue context."
        ) from error
    return context_path


def _collect_comments(
    invocation: GitHubInvocation,
    client: GitHubClient,
    *,
    max_comments: int,
    max_comment_pages: int,
) -> _CommentCollection:
    if max_comments == 0:
        return _CommentCollection(comments=(), truncated=True)

    first_page = client.list_issue_comments(
        invocation.repository,
        invocation.issue.number,
        page=1,
        per_page=100,
    )
    oldest_page = max(1, first_page.last_page - max_comment_pages + 1)
    page_limit_reached = oldest_page > 1
    selected: list[GitHubIssueCommentSnapshot] = []
    comment_limit_reached = False

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
            if not _is_eligible_comment(invocation, comment):
                continue
            if len(selected) < max_comments:
                selected.append(comment)
                continue
            comment_limit_reached = True
            break
        if comment_limit_reached:
            break

    selected.sort(key=lambda item: (item.created_at, item.comment_id))
    clarification_comments = [
        comment
        for comment in selected
        if comment.author_login == STATUS_BOT_LOGIN
        and has_sage_clarification_marker(comment.body)
    ]
    if len(clarification_comments) > 1:
        latest_id = clarification_comments[-1].comment_id
        selected = [
            comment
            for comment in selected
            if not (
                comment.author_login == STATUS_BOT_LOGIN
                and has_sage_clarification_marker(comment.body)
                and comment.comment_id != latest_id
            )
        ]
    return _CommentCollection(
        comments=tuple(selected),
        truncated=page_limit_reached or comment_limit_reached,
    )


def _is_eligible_comment(
    invocation: GitHubInvocation,
    comment: GitHubIssueCommentSnapshot,
) -> bool:
    if comment.comment_id == invocation.comment.comment_id:
        return False
    if comment.created_at > invocation.comment.created_at:
        return False
    if parse_command(comment.body) is not None:
        return False
    if comment.author_login != STATUS_BOT_LOGIN:
        return True
    if not has_sage_status_marker(comment.body):
        return True
    return has_sage_clarification_marker(comment.body)


def _validate_current_issue(
    invocation: GitHubInvocation,
    issue: GitHubIssueSnapshot,
) -> None:
    if (
        issue.number != invocation.issue.number
        or issue.html_url.rstrip("/") != invocation.issue.html_url.rstrip("/")
    ):
        raise GitHubContextError(
            "Current GitHub Issue does not match the validated invocation."
        )


def _validate_limits(
    *,
    max_comments: int,
    max_comment_pages: int,
    max_context_chars: int,
) -> None:
    if not 0 <= max_comments <= MAX_CONTEXT_COMMENTS:
        raise ValueError(
            f"Maximum context comments must be between 0 and "
            f"{MAX_CONTEXT_COMMENTS}."
        )
    if not 1 <= max_comment_pages <= MAX_COMMENT_PAGES:
        raise ValueError(
            f"Maximum comment pages must be between 1 and {MAX_COMMENT_PAGES}."
        )
    if not MIN_CONTEXT_CHARS <= max_context_chars <= MAX_CONTEXT_CHARS:
        raise ValueError(
            f"Context limit must be between {MIN_CONTEXT_CHARS} and "
            f"{MAX_CONTEXT_CHARS} characters."
        )
