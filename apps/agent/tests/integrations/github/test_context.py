from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sage.errors import GitHubContextError
from sage.integrations.github.api_models import (
    GitHubCommentPage,
    GitHubIssueCommentSnapshot,
    GitHubIssueSnapshot,
)
from sage.integrations.github.context import (
    GitHubIssueContext,
    build_issue_context,
    materialize_issue_context,
)
from sage.integrations.github.events import load_issue_comment_event
from sage.integrations.github.models import GitHubInvocation, GitHubRepository
from sage.integrations.github.status import invocation_marker

FIXTURES = Path(__file__).parents[2] / "fixtures" / "github"
BASE_SHA = "a" * 40


@dataclass(slots=True)
class FakeContextClient:
    issue: GitHubIssueSnapshot
    pages: dict[int, GitHubCommentPage]
    calls: list[str] = field(default_factory=list)

    def get_issue(
        self,
        repository: GitHubRepository,
        issue_number: int,
    ) -> GitHubIssueSnapshot:
        self.calls.append("issue")
        return self.issue

    def list_issue_comments(
        self,
        repository: GitHubRepository,
        issue_number: int,
        *,
        page: int = 1,
        per_page: int = 100,
    ) -> GitHubCommentPage:
        self.calls.append(f"comments:{page}")
        assert per_page == 100
        return self.pages[page]


def test_build_context_uses_current_issue_and_filters_discussion() -> None:
    invocation = _invocation()
    comments = (
        _comment(10, "Earlier diagnosis", "alice", hour=9),
        _comment(11, "/sage fix", "maintainer", hour=9, minute=15),
        _comment(
            12,
            f"{invocation_marker(999)}\nSage status",
            "github-actions[bot]",
            hour=9,
            minute=30,
        ),
        _comment(14, "Recent reproduction", "bob", hour=10, minute=15),
        _comment(1001, "/sage solve", "maintainer", hour=10, minute=30),
        _comment(15, "Posted after invocation", "carol", hour=11),
    )
    client = FakeContextClient(
        issue=_issue(title="Current issue title", body="Current issue body"),
        pages={1: _page(1, comments=comments)},
    )

    context = build_issue_context(
        invocation,
        client,
        max_comments=20,
        max_comment_pages=5,
        max_context_chars=10_000,
    )

    assert context.included_comment_ids == (10, 14)
    assert context.history_truncated is False
    assert client.calls == ["issue", "comments:1"]
    assert "Repository: 24aysh/example" in context.markdown
    assert "Issue: #17" in context.markdown
    assert "Command: /sage solve" in context.markdown
    assert "Invoked at: 2026-08-19T10:30:00Z" in context.markdown
    assert "Current issue title" in context.markdown
    assert "Current issue body" in context.markdown
    assert context.markdown.index("Earlier diagnosis") < context.markdown.index(
        "Recent reproduction"
    )
    assert "/sage fix" not in context.markdown
    assert "Sage status" not in context.markdown
    assert "Posted after invocation" not in context.markdown


def test_build_context_renders_explicit_empty_sections() -> None:
    client = FakeContextClient(
        issue=_issue(title="No description", body=""),
        pages={1: _page(1)},
    )

    context = build_issue_context(
        _invocation(),
        client,
        max_comments=20,
        max_comment_pages=5,
        max_context_chars=4_000,
    )

    assert "_(No description provided.)_" in context.markdown
    assert "_(No eligible prior discussion.)_" in context.markdown
    assert context.history_truncated is False


def test_build_context_selects_newest_comments_and_discloses_page_limit() -> None:
    client = FakeContextClient(
        issue=_issue(),
        pages={
            1: _page(1, last_page=4, comments=(_comment(10, "Old", "old"),)),
            4: _page(
                4,
                comments=(
                    _comment(40, "Newest eligible", "new", hour=10, minute=20),
                    _comment(41, "Too late", "late", hour=11),
                ),
            ),
            3: _page(
                3,
                last_page=4,
                comments=(
                    _comment(30, "Older eligible", "older", hour=10, minute=10),
                    _comment(31, "Second newest", "second", hour=10, minute=15),
                ),
            ),
        },
    )

    context = build_issue_context(
        _invocation(),
        client,
        max_comments=2,
        max_comment_pages=2,
        max_context_chars=6_000,
    )

    assert client.calls == ["issue", "comments:1", "comments:4", "comments:3"]
    assert context.included_comment_ids == (31, 40)
    assert "Second newest" in context.markdown
    assert "Newest eligible" in context.markdown
    assert "Older eligible" not in context.markdown
    assert "Too late" not in context.markdown
    assert "Some discussion was omitted" in context.markdown
    assert context.history_truncated is True


def test_zero_comment_limit_skips_comment_api_and_discloses_omission() -> None:
    client = FakeContextClient(issue=_issue(), pages={})

    context = build_issue_context(
        _invocation(),
        client,
        max_comments=0,
        max_comment_pages=5,
        max_context_chars=4_000,
    )

    assert client.calls == ["issue"]
    assert context.included_comment_ids == ()
    assert context.history_truncated is True
    assert "Some discussion was omitted" in context.markdown


def test_context_enforces_total_and_section_bounds_with_visible_markers() -> None:
    hidden_marker = "<!-- sage-invocation:123 -->"
    client = FakeContextClient(
        issue=_issue(
            title="Title " + ("T" * 1_000),
            body=(
                f"Beginning\x00{hidden_marker}"
                + ("B" * 40_000)
                + "TAIL_ACCEPTANCE"
            ),
        ),
        pages={
            1: _page(
                1,
                comments=(
                    _comment(20, "C" * 8_000, "alice", hour=9),
                    _comment(21, "D" * 8_000, "bob", hour=10),
                ),
            )
        },
    )

    context = build_issue_context(
        _invocation(),
        client,
        max_comments=20,
        max_comment_pages=5,
        max_context_chars=2_000,
    )

    assert len(context.markdown) <= 2_000
    assert "[title truncated]" in context.markdown
    assert "Description truncated by Sage" in context.markdown
    assert "TAIL_ACCEPTANCE" in context.markdown
    assert hidden_marker not in context.markdown
    assert "&lt;!-- sage-invocation:123 -->" in context.markdown
    assert "\x00" not in context.markdown
    assert "Some discussion was omitted" in context.markdown
    assert context.included_comment_ids == (21,)
    assert context.history_truncated is True


def test_build_context_rejects_mismatched_current_issue() -> None:
    client = FakeContextClient(
        issue=GitHubIssueSnapshot(
            number=18,
            title="Wrong issue",
            body="",
            html_url="https://github.com/24aysh/example/issues/18",
        ),
        pages={},
    )

    with pytest.raises(GitHubContextError, match="does not match"):
        build_issue_context(
            _invocation(),
            client,
            max_comments=20,
            max_comment_pages=5,
            max_context_chars=4_000,
        )

    assert client.calls == ["issue"]


def test_materialize_context_writes_outside_target_checkout(tmp_path: Path) -> None:
    checkout = tmp_path / "target"
    checkout.mkdir()
    context = GitHubIssueContext(
        issue_number=17,
        invocation_comment_id=1001,
        markdown="# Task\n",
        history_truncated=False,
    )

    context_path = materialize_issue_context(
        context,
        context_dir=tmp_path / "runner" / "context",
        target_checkout=checkout,
    )

    assert context_path.name == "sage-issue-17-comment-1001.md"
    assert context_path.read_text(encoding="utf-8") == "# Task\n"
    assert checkout not in context_path.parents


def test_materialize_context_rejects_directory_inside_checkout(
    tmp_path: Path,
) -> None:
    checkout = tmp_path / "target"
    checkout.mkdir()
    context = GitHubIssueContext(
        issue_number=17,
        invocation_comment_id=1001,
        markdown="# Task\n",
        history_truncated=False,
    )

    with pytest.raises(GitHubContextError, match="outside"):
        materialize_issue_context(
            context,
            context_dir=checkout / ".sage-context",
            target_checkout=checkout,
        )


def test_materialize_context_wraps_filesystem_failures(tmp_path: Path) -> None:
    checkout = tmp_path / "target"
    checkout.mkdir()
    context_root = tmp_path / "not-a-directory"
    context_root.write_text("occupied", encoding="utf-8")
    context = GitHubIssueContext(
        issue_number=17,
        invocation_comment_id=1001,
        markdown="# Task\n",
        history_truncated=False,
    )

    with pytest.raises(GitHubContextError, match="Unable to materialize"):
        materialize_issue_context(
            context,
            context_dir=context_root,
            target_checkout=checkout,
        )


def _invocation() -> GitHubInvocation:
    return load_issue_comment_event(
        {
            "GITHUB_EVENT_NAME": "issue_comment",
            "GITHUB_EVENT_PATH": str(FIXTURES / "issue_solve.json"),
            "GITHUB_REPOSITORY": "24aysh/example",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_RUN_ID": "9001",
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_SHA": BASE_SHA,
        }
    )


def _issue(
    *,
    title: str = "Current title",
    body: str = "Current body",
) -> GitHubIssueSnapshot:
    return GitHubIssueSnapshot(
        number=17,
        title=title,
        body=body,
        html_url="https://github.com/24aysh/example/issues/17",
    )


def _page(
    page: int,
    *,
    last_page: int | None = None,
    comments: tuple[GitHubIssueCommentSnapshot, ...] = (),
) -> GitHubCommentPage:
    return GitHubCommentPage(
        comments=comments,
        page=page,
        last_page=last_page or page,
    )


def _comment(
    comment_id: int,
    body: str,
    author: str,
    *,
    hour: int = 8,
    minute: int = 0,
) -> GitHubIssueCommentSnapshot:
    return GitHubIssueCommentSnapshot(
        comment_id=comment_id,
        body=body,
        author_login=author,
        created_at=datetime(2026, 8, 19, hour, minute, tzinfo=UTC),
        html_url=(
            "https://github.com/24aysh/example/issues/17"
            f"#issuecomment-{comment_id}"
        ),
    )
