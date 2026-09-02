from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sage.errors import GitHubApiError, GitHubGateError
from sage.integrations.github.api_models import (
    GitHubBranchSnapshot,
    GitHubCommentPage,
    GitHubIssueCommentSnapshot,
    GitHubPermission,
    GitHubPullRequestSnapshot,
)
from sage.integrations.github.events import load_issue_comment_event
from sage.integrations.github.gate import evaluate_gate
from sage.integrations.github.models import GateOutcome
from sage.integrations.github.models import GitHubInvocation, GitHubRepository
from sage.integrations.github.status import invocation_marker

FIXTURES = Path(__file__).parents[2] / "fixtures" / "github"
BASE_SHA = "a" * 40


@dataclass(slots=True)
class FakeGitHubClient:
    permission: str = "write"
    pull_requests: tuple[GitHubPullRequestSnapshot, ...] = ()
    branch: GitHubBranchSnapshot | None = None
    comment_pages: list[GitHubCommentPage] = field(default_factory=list)
    create_results: list[GitHubIssueCommentSnapshot | Exception] = field(
        default_factory=list
    )
    calls: list[str] = field(default_factory=list)
    created_bodies: list[str] = field(default_factory=list)
    updated: list[tuple[int, str]] = field(default_factory=list)

    def get_repository_permission(
        self,
        repository: GitHubRepository,
        actor: str,
    ) -> GitHubPermission:
        self.calls.append("permission")
        return GitHubPermission(permission=self.permission)

    def list_open_pull_requests(
        self,
        repository: GitHubRepository,
        *,
        head_branch: str,
        base_branch: str,
    ) -> tuple[GitHubPullRequestSnapshot, ...]:
        self.calls.append("pull_requests")
        return self.pull_requests

    def get_branch(
        self,
        repository: GitHubRepository,
        branch: str,
    ) -> GitHubBranchSnapshot | None:
        self.calls.append("branch")
        return self.branch

    def list_issue_comments(
        self,
        repository: GitHubRepository,
        issue_number: int,
        *,
        page: int = 1,
        per_page: int = 100,
    ) -> GitHubCommentPage:
        self.calls.append(f"comments:{page}")
        if self.comment_pages:
            result = self.comment_pages.pop(0)
            assert result.page == page
            return result
        return _page(page=page)

    def create_issue_comment(
        self,
        repository: GitHubRepository,
        issue_number: int,
        body: str,
    ) -> GitHubIssueCommentSnapshot:
        self.calls.append("create_comment")
        self.created_bodies.append(body)
        if self.create_results:
            result = self.create_results.pop(0)
            if isinstance(result, Exception):
                raise result
            return result
        return _status_comment(7001, body=body)

    def update_issue_comment(
        self,
        repository: GitHubRepository,
        comment_id: int,
        body: str,
    ) -> GitHubIssueCommentSnapshot:
        self.calls.append("update_comment")
        self.updated.append((comment_id, body))
        return _status_comment(comment_id, body=body)


@pytest.mark.parametrize(
    "fixture",
    ["issue_ordinary_comment.json", "pull_request_solve.json"],
)
def test_unsupported_invocation_is_ignored_without_api_calls(fixture: str) -> None:
    client = FakeGitHubClient()

    result = evaluate_gate(
        _invocation(fixture),
        client,
        max_comment_pages=5,
    )

    assert result.outcome is GateOutcome.IGNORED
    assert result.should_run is False
    assert result.status_comment_id is None
    assert client.calls == []


def test_unauthorized_command_cannot_reach_duplicate_or_solve_decision() -> None:
    client = FakeGitHubClient(permission="read")

    result = evaluate_gate(
        _invocation(),
        client,
        max_comment_pages=5,
    )

    assert result.outcome is GateOutcome.UNAUTHORIZED
    assert result.should_run is False
    assert result.status_comment_id == 7001
    assert client.calls == ["permission", "comments:1", "create_comment"]
    assert "request accepted" not in client.created_bodies[0]
    assert "No solve was started" in client.created_bodies[0]


def test_authorized_new_issue_is_accepted_only_after_duplicate_checks() -> None:
    client = FakeGitHubClient(permission="admin")

    result = evaluate_gate(
        _invocation(),
        client,
        max_comment_pages=5,
    )

    assert result.outcome is GateOutcome.ACCEPTED
    assert result.should_run is True
    assert result.base_sha == BASE_SHA
    assert result.base_branch == "main"
    assert result.issue_number == 17
    assert client.calls == [
        "permission",
        "pull_requests",
        "branch",
        "comments:1",
        "create_comment",
    ]
    assert invocation_marker(1001) in client.created_bodies[0]


def test_existing_pull_request_cannot_reach_solve_decision() -> None:
    client = FakeGitHubClient(pull_requests=(_pull_request(),))

    result = evaluate_gate(
        _invocation(),
        client,
        max_comment_pages=5,
    )

    assert result.outcome is GateOutcome.EXISTING_PULL_REQUEST
    assert result.should_run is False
    assert result.existing_pull_request_url.endswith("/pull/21")
    assert "branch" not in client.calls
    assert "an open Pull Request" in client.created_bodies[0]


def test_existing_branch_without_pull_request_is_blocked() -> None:
    client = FakeGitHubClient(
        branch=GitHubBranchSnapshot(
            name="sage/issue-17",
            sha="b" * 40,
            protected=False,
        )
    )

    result = evaluate_gate(
        _invocation(),
        client,
        max_comment_pages=5,
    )

    assert result.outcome is GateOutcome.BLOCKED_EXISTING_BRANCH
    assert result.should_run is False
    assert "will not overwrite" in client.created_bodies[0]


def test_multiple_matching_pull_requests_fail_closed() -> None:
    client = FakeGitHubClient(pull_requests=(_pull_request(21), _pull_request(22)))

    with pytest.raises(GitHubGateError, match="multiple open Pull Requests"):
        evaluate_gate(_invocation(), client, max_comment_pages=5)

    assert client.calls == ["permission", "pull_requests"]


def test_gate_reuses_only_bot_authored_status_marker() -> None:
    marker = invocation_marker(1001)
    client = FakeGitHubClient(
        comment_pages=[
            _page(
                comments=(
                    _status_comment(7000, body=marker, author="maintainer"),
                    _status_comment(7001, body=marker),
                )
            )
        ]
    )

    result = evaluate_gate(_invocation(), client, max_comment_pages=5)

    assert result.status_comment_id == 7001
    assert client.updated[0][0] == 7001
    assert "create_comment" not in client.calls


def test_gate_scans_newest_comment_pages_with_a_bound() -> None:
    marker = invocation_marker(1001)
    client = FakeGitHubClient(
        comment_pages=[
            _page(page=1, last_page=4),
            _page(page=4),
            _page(page=3, comments=(_status_comment(7001, body=marker),)),
        ]
    )

    result = evaluate_gate(_invocation(), client, max_comment_pages=2)

    assert result.status_comment_id == 7001
    assert [call for call in client.calls if call.startswith("comments:")] == [
        "comments:1",
        "comments:4",
        "comments:3",
    ]


def test_ambiguous_status_create_is_reconciled_by_marker_before_retry() -> None:
    marker = invocation_marker(1001)
    client = FakeGitHubClient(
        comment_pages=[
            _page(),
            _page(comments=(_status_comment(7001, body=marker),)),
        ],
        create_results=[
            GitHubApiError("ambiguous create", ambiguous=True),
        ],
    )

    result = evaluate_gate(_invocation(), client, max_comment_pages=5)

    assert result.status_comment_id == 7001
    assert client.calls.count("create_comment") == 1
    assert client.calls.count("comments:1") == 2


def test_ambiguous_status_create_retries_once_when_marker_is_absent() -> None:
    client = FakeGitHubClient(
        comment_pages=[_page(), _page()],
        create_results=[
            GitHubApiError("ambiguous create", ambiguous=True),
            _status_comment(7001, body=invocation_marker(1001)),
        ],
    )

    result = evaluate_gate(_invocation(), client, max_comment_pages=5)

    assert result.status_comment_id == 7001
    assert client.calls.count("create_comment") == 2


def test_non_ambiguous_status_create_is_not_retried() -> None:
    client = FakeGitHubClient(
        create_results=[GitHubApiError("forbidden", status_code=403)]
    )

    with pytest.raises(GitHubApiError, match="forbidden"):
        evaluate_gate(_invocation(), client, max_comment_pages=5)

    assert client.calls.count("create_comment") == 1
    assert client.calls.count("comments:1") == 1


def _invocation(fixture: str = "issue_solve.json") -> GitHubInvocation:
    return load_issue_comment_event(
        {
            "GITHUB_EVENT_NAME": "issue_comment",
            "GITHUB_EVENT_PATH": str(FIXTURES / fixture),
            "GITHUB_REPOSITORY": "24aysh/example",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_RUN_ID": "9001",
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_SHA": BASE_SHA,
        }
    )


def _page(
    *,
    page: int = 1,
    last_page: int | None = None,
    comments: tuple[GitHubIssueCommentSnapshot, ...] = (),
) -> GitHubCommentPage:
    return GitHubCommentPage(
        comments=comments,
        page=page,
        last_page=last_page or page,
    )


def _status_comment(
    comment_id: int,
    *,
    body: str,
    author: str = "github-actions[bot]",
) -> GitHubIssueCommentSnapshot:
    return GitHubIssueCommentSnapshot(
        comment_id=comment_id,
        body=body,
        author_login=author,
        created_at=datetime(2026, 8, 19, 10, 0, tzinfo=UTC),
        html_url=(
            "https://github.com/24aysh/example/issues/17"
            f"#issuecomment-{comment_id}"
        ),
    )


def _pull_request(number: int = 21) -> GitHubPullRequestSnapshot:
    return GitHubPullRequestSnapshot(
        number=number,
        html_url=f"https://github.com/24aysh/example/pull/{number}",
        state="open",
        draft=True,
        head_ref="sage/issue-17",
        base_ref="main",
    )
