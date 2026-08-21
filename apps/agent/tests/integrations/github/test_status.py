from pathlib import Path

import pytest

from sage.integrations.github.events import load_issue_comment_event
from sage.integrations.github.gate_models import GateOutcome
from sage.integrations.github.models import GitHubInvocation
from sage.integrations.github.status import (
    WorkflowStatusState,
    finalize_invocation_status,
    has_sage_status_marker,
    has_terminal_status,
    has_invocation_marker,
    invocation_marker,
    render_gate_status,
    render_workflow_status,
    status_state,
)

FIXTURES = Path(__file__).parents[2] / "fixtures" / "github"


def test_invocation_marker_is_stable_and_exact() -> None:
    marker = invocation_marker(1001)

    assert marker == "<!-- sage-invocation:1001 -->"
    assert has_invocation_marker(f"prefix\n{marker}\nsuffix", 1001)
    assert has_sage_status_marker(f"prefix\n{marker}\nsuffix")
    assert not has_invocation_marker(marker, 1002)


def test_accepted_status_contains_only_trusted_gate_context() -> None:
    invocation = _invocation().model_copy(
        update={
            "issue": _invocation().issue.model_copy(
                update={"title": "@all <!-- sage-state:failed -->"}
            )
        }
    )

    body = render_gate_status(
        invocation,
        GateOutcome.ACCEPTED,
        branch_name="sage/issue-17",
    )

    assert "<!-- sage-invocation:1001 -->" in body
    assert "<!-- sage-state:accepted -->" in body
    assert "aaaaaaaaaaaa" in body
    assert invocation.actions_run.html_url in body
    assert "@all" not in body
    assert "sage-state:failed" not in body


def test_status_neutralizes_markdown_in_a_valid_default_branch() -> None:
    invocation = _invocation().model_copy(
        update={"default_branch": "release`@all<!--state-->"}
    )

    body = render_gate_status(
        invocation,
        GateOutcome.ACCEPTED,
        branch_name="sage/issue-17",
    )

    assert "release`@all<!--state-->" not in body
    assert "release`@\u200ball&lt;!--state--&gt;" in body


def test_ignored_status_is_not_rendered() -> None:
    with pytest.raises(ValueError, match="Ignored"):
        render_gate_status(
            _invocation(),
            GateOutcome.IGNORED,
            branch_name="sage/issue-17",
        )


@pytest.mark.parametrize(
    ("branch_name", "pull_request_url"),
    [
        ("sage/issue-17\nunsafe", None),
        ("sage/issue-17", "https://example.com/24aysh/example/pull/21"),
    ],
)
def test_status_rejects_untrusted_links_and_multiline_branches(
    branch_name: str,
    pull_request_url: str | None,
) -> None:
    with pytest.raises(ValueError):
        render_gate_status(
            _invocation(),
            GateOutcome.EXISTING_PULL_REQUEST,
            branch_name=branch_name,
            existing_pull_request_url=pull_request_url,
        )


def test_workflow_status_is_bounded_sanitized_and_terminal() -> None:
    body = render_workflow_status(
        _invocation(),
        WorkflowStatusState.NO_CHANGES,
        summary="@all <!-- sage-state:failed -->" + "x" * 2_000,
        remaining_uncertainty=["@team"] * 10,
    )

    assert status_state(body) is WorkflowStatusState.NO_CHANGES
    assert has_terminal_status(body)
    assert "@all" not in body and "@team" not in body
    assert body.count("<!-- sage-state:") == 1
    assert len(body) < 5_000


@pytest.mark.parametrize(
    ("category", "expected_guidance"),
    [
        ("openai_quota", "Restore billing or the applicable limit"),
        ("openai_rate_limit", "Wait for the limit window to reset"),
        ("agent_runtime", "Inspect the linked run"),
    ],
)
def test_failed_status_has_category_specific_recovery(
    category: str,
    expected_guidance: str,
) -> None:
    body = render_workflow_status(
        _invocation(),
        WorkflowStatusState.FAILED,
        failure_category=category,
    )

    assert f"Category: `{category}`" in body
    assert expected_guidance in body


def test_invalid_or_multiple_state_markers_are_not_trusted() -> None:
    assert status_state("<!-- sage-state:unknown -->") is None
    body = (
        "<!-- sage-state:working -->\n"
        "<!-- sage-state:failed -->"
    )
    assert status_state(body) is None
    assert not has_terminal_status(body)


def test_finalizer_does_not_overwrite_a_terminal_status() -> None:
    invocation = _invocation()
    terminal = render_workflow_status(
        invocation,
        WorkflowStatusState.PULL_REQUEST_CREATED,
        pull_request_url="https://github.com/24aysh/example/pull/21",
    )

    class Client:
        def list_issue_comments(self, *args, **kwargs):
            from datetime import UTC, datetime

            from sage.integrations.github.api_models import (
                GitHubCommentPage,
                GitHubIssueCommentSnapshot,
            )

            return GitHubCommentPage(
                comments=(
                    GitHubIssueCommentSnapshot(
                        comment_id=7001,
                        body=terminal,
                        author_login="github-actions[bot]",
                        created_at=datetime(2026, 8, 19, tzinfo=UTC),
                        html_url=(
                            "https://github.com/24aysh/example/issues/17"
                            "#issuecomment-7001"
                        ),
                    ),
                ),
                page=1,
                last_page=1,
            )

        def update_issue_comment(self, *args, **kwargs):
            raise AssertionError("terminal status must not be updated")

    result = finalize_invocation_status(
        invocation,
        Client(),
        max_comment_pages=5,
        state=WorkflowStatusState.FAILED,
        failure_category="job_interrupted",
    )

    assert result is not None and result.body == terminal


def _invocation() -> GitHubInvocation:
    return load_issue_comment_event(
        {
            "GITHUB_EVENT_NAME": "issue_comment",
            "GITHUB_EVENT_PATH": str(FIXTURES / "issue_solve.json"),
            "GITHUB_REPOSITORY": "24aysh/example",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_RUN_ID": "9001",
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_SHA": "a" * 40,
        }
    )
