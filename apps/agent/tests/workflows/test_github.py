import json
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sage.config import Settings
from sage.domain.solve import SolveOutcome, SolveResult
from sage.errors import (
    AgentRuntimeError,
    GitHubPublicationError,
)
from sage.integrations.github.api_models import (
    GitHubBranchSnapshot,
    GitHubCommentPage,
    GitHubIssueCommentSnapshot,
    GitHubIssueSnapshot,
    GitHubPermission,
    GitHubPullRequestSnapshot,
)
from sage.integrations.github.config import GitHubSettings
from sage.integrations.github.events import load_issue_comment_event
from sage.integrations.github.models import GateOutcome
from sage.integrations.github.models import GitHubInvocation
from sage.integrations.github.publication import (
    BaseMovement,
    PublicationOutcome,
    PublicationResult,
)
from sage.integrations.github.status import (
    WorkflowStatusState,
    render_gate_status,
    status_state,
)
from sage.workflows.github import (
    GitHubWorkflowOutcome,
    classify_github_failure,
    finalize_github_issue,
    run_github_issue,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "github"


@dataclass(slots=True)
class FakeClient:
    invocation: GitHubInvocation
    permission: str = "write"
    pull_requests: tuple[GitHubPullRequestSnapshot, ...] = ()
    branch: GitHubBranchSnapshot | None = None
    status_body: str = ""
    status_comment_id: int = 7001
    updates: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.status_body:
            self.status_body = render_gate_status(
                self.invocation,
                GateOutcome.ACCEPTED,
                branch_name="sage/issue-17",
            )

    def get_repository_permission(self, repository, actor):
        return GitHubPermission(permission=self.permission)

    def list_open_pull_requests(self, repository, *, head_branch, base_branch):
        return self.pull_requests

    def get_branch(self, repository, branch):
        return self.branch

    def get_issue(self, repository, issue_number):
        return GitHubIssueSnapshot(
            number=self.invocation.issue.number,
            title=self.invocation.issue.title,
            body=self.invocation.issue.body,
            html_url=self.invocation.issue.html_url,
        )

    def list_issue_comments(self, repository, issue_number, *, page=1, per_page=100):
        return GitHubCommentPage(
            comments=(self._status(),),
            page=1,
            last_page=1,
        )

    def update_issue_comment(self, repository, comment_id, body):
        assert comment_id == self.status_comment_id
        self.status_body = body
        self.updates.append(body)
        return self._status()

    def _status(self) -> GitHubIssueCommentSnapshot:
        return GitHubIssueCommentSnapshot(
            comment_id=self.status_comment_id,
            body=self.status_body,
            author_login="github-actions[bot]",
            created_at=datetime(2026, 8, 19, 10, 1, tzinfo=UTC),
            html_url=(
                f"{self.invocation.issue.html_url}#issuecomment-"
                f"{self.status_comment_id}"
            ),
        )


def _pull_request() -> GitHubPullRequestSnapshot:
    return GitHubPullRequestSnapshot(
        number=21,
        html_url="https://github.com/24aysh/example/pull/21",
        state="open",
        draft=True,
        head_ref="sage/issue-17",
        base_ref="main",
    )


def test_no_change_uses_exact_sha_without_calling_publisher(tmp_path: Path) -> None:
    checkout, base_sha = _checkout(tmp_path)
    invocation = _invocation(base_sha)
    client = FakeClient(invocation)
    factories: list[str] = []

    async def solve_runner(request, orchestrator, settings):
        assert request.repo_path == checkout.resolve()
        assert request.base_ref == base_sha
        assert request.issue_path.parent == (tmp_path / "context").resolve()
        assert "Base commit: " + base_sha in request.issue_path.read_text(
            encoding="utf-8"
        )
        return _solve_result(tmp_path, base_sha, diff="", changed_files=[])

    def unexpected_publisher(*args, **kwargs):
        raise AssertionError("No-change path must not call publication.")

    result = _run(
        invocation,
        client,
        tmp_path,
        checkout,
        solve_runner=solve_runner,
        settings_factory=lambda: factories.append("settings") or _settings(tmp_path),
        orchestrator_factory=lambda settings: factories.append("orchestrator") or object(),
        publisher=unexpected_publisher,
    )

    assert result.outcome is GitHubWorkflowOutcome.NO_CHANGES
    assert factories == ["settings", "orchestrator"]
    assert [status_state(body) for body in client.updates] == [
        WorkflowStatusState.WORKING,
        WorkflowStatusState.NO_CHANGES,
    ]
    provenance = json.loads(
        (tmp_path / "diagnostics" / "github.json").read_text(encoding="utf-8")
    )
    assert provenance["outcome"] == "no_changes"
    assert "github_token" not in provenance
    assert "openai" not in json.dumps(provenance).lower()
    assert (tmp_path / "run" / "github.json").is_file()


def test_non_empty_result_hands_authoritative_candidate_to_publisher(tmp_path: Path) -> None:
    checkout, base_sha = _checkout(tmp_path)
    invocation = _invocation(base_sha)
    client = FakeClient(invocation)
    candidate = _solve_result(
        tmp_path,
        base_sha,
        diff="diff --git a/app.py b/app.py\n",
        changed_files=["app.py"],
    )
    published_calls: list[SolveResult] = []

    async def solve_runner(request, orchestrator, settings):
        return candidate

    def publisher(invocation_value, result, api, **kwargs):
        assert invocation_value is invocation
        assert api is client
        assert kwargs["github_token"] == "github-secret"
        assert kwargs["runner_temp"] == tmp_path / "runner"
        published_calls.append(result)
        return PublicationResult(
            outcome=PublicationOutcome.PULL_REQUEST_CREATED,
            branch_name="sage/issue-17",
            original_base_sha=base_sha,
            current_base_sha=base_sha,
            base_movement=BaseMovement.UNCHANGED,
            pull_request_number=21,
            pull_request_url="https://github.com/24aysh/example/pull/21",
        )

    result = _run(
        invocation,
        client,
        tmp_path,
        checkout,
        solve_runner=solve_runner,
        publisher=publisher,
    )

    assert result.outcome is GitHubWorkflowOutcome.PULL_REQUEST_CREATED
    assert published_calls == [candidate]
    assert status_state(client.status_body) is WorkflowStatusState.PULL_REQUEST_CREATED
    assert "https://github.com/24aysh/example/pull/21" in client.status_body
    provenance = json.loads(
        (tmp_path / "diagnostics" / "github.json").read_text(encoding="utf-8")
    )
    assert provenance["pull_request_number"] == 21
    assert provenance["local_run_id"] == "run-id"


def test_human_required_after_start_is_terminal_and_never_publishes(
    tmp_path: Path,
) -> None:
    checkout, base_sha = _checkout(tmp_path)
    invocation = _invocation(base_sha)
    client = FakeClient(invocation)
    terminal = _solve_result(
        tmp_path,
        base_sha,
        diff="",
        changed_files=[],
    ).model_copy(
        update={
            "outcome": SolveOutcome.HUMAN_REQUIRED_AFTER_START,
            "summary": "Solver needs a maintainer decision after implementation began.",
        }
    )

    async def solve_runner(request, orchestrator, settings):
        return terminal

    result = _run(
        invocation,
        client,
        tmp_path,
        checkout,
        solve_runner=solve_runner,
        publisher=lambda *args, **kwargs: pytest.fail("publisher called"),
    )

    assert result.outcome is GitHubWorkflowOutcome.HUMAN_REQUIRED_AFTER_START
    assert status_state(client.status_body) is WorkflowStatusState.HUMAN_REQUIRED_AFTER_START
    assert "maintainer decision" in client.status_body


@pytest.mark.parametrize(
    ("permission", "pull_requests", "branch", "expected"),
    [
        ("read", (), None, GitHubWorkflowOutcome.UNAUTHORIZED),
        ("write", (_pull_request(),), None, GitHubWorkflowOutcome.EXISTING_PULL_REQUEST),
        (
            "write",
            (),
            GitHubBranchSnapshot(
                name="sage/issue-17",
                sha="b" * 40,
                protected=False,
            ),
            GitHubWorkflowOutcome.BLOCKED_EXISTING_BRANCH,
        ),
    ],
)
def test_solve_time_gate_stops_before_model_construction(
    tmp_path: Path,
    permission: str,
    pull_requests: tuple[GitHubPullRequestSnapshot, ...],
    branch: GitHubBranchSnapshot | None,
    expected: GitHubWorkflowOutcome,
) -> None:
    checkout, base_sha = _checkout(tmp_path)
    invocation = _invocation(base_sha)
    client = FakeClient(
        invocation,
        permission=permission,
        pull_requests=pull_requests,
        branch=branch,
    )

    def forbidden_settings():
        raise AssertionError("Model settings must not be loaded.")

    async def forbidden_solve(request, orchestrator, settings):
        raise AssertionError("Solver must not run.")

    result = _run(
        invocation,
        client,
        tmp_path,
        checkout,
        solve_runner=forbidden_solve,
        settings_factory=forbidden_settings,
    )

    assert result.outcome is expected
    assert status_state(client.status_body) is WorkflowStatusState.FAILED
    assert (tmp_path / "diagnostics" / "github.json").is_file()


def test_runtime_failure_is_safely_classified_and_does_not_publish(tmp_path: Path) -> None:
    checkout, base_sha = _checkout(tmp_path)
    invocation = _invocation(base_sha)
    client = FakeClient(invocation)

    async def failing_solve(request, orchestrator, settings):
        raise AgentRuntimeError("provider detail must stay in logs")

    with pytest.raises(AgentRuntimeError):
        _run(
            invocation,
            client,
            tmp_path,
            checkout,
            solve_runner=failing_solve,
            publisher=lambda *args, **kwargs: pytest.fail("publisher called"),
        )

    assert status_state(client.status_body) is WorkflowStatusState.FAILED
    assert "agent_runtime" in client.status_body
    assert "provider detail" not in client.status_body
    provenance = (tmp_path / "diagnostics" / "github.json").read_text(
        encoding="utf-8"
    )
    assert "failed:agent_runtime" in provenance
    assert "provider detail" not in provenance


def test_publication_failure_preserves_safe_terminal_and_run_artifacts(tmp_path: Path) -> None:
    checkout, base_sha = _checkout(tmp_path)
    invocation = _invocation(base_sha)
    client = FakeClient(invocation)

    async def solve_runner(request, orchestrator, settings):
        return _solve_result(
            tmp_path,
            base_sha,
            diff="diff --git a/app.py b/app.py\n",
            changed_files=["app.py"],
        )

    def failing_publisher(*args, **kwargs):
        raise GitHubPublicationError("unsafe git stderr")

    with pytest.raises(GitHubPublicationError):
        _run(
            invocation,
            client,
            tmp_path,
            checkout,
            solve_runner=solve_runner,
            publisher=failing_publisher,
        )

    assert status_state(client.status_body) is WorkflowStatusState.FAILED
    assert "unsafe git stderr" not in client.status_body
    assert (tmp_path / "diagnostics" / "metadata.json").is_file()


def test_finalizer_preserves_terminal_status_and_reconciles_pull_request(tmp_path: Path) -> None:
    checkout, base_sha = _checkout(tmp_path)
    invocation = _invocation(base_sha)
    client = FakeClient(invocation, pull_requests=(_pull_request(),))

    finalize_github_issue(invocation, client, max_comment_pages=5)

    assert status_state(client.status_body) is WorkflowStatusState.PULL_REQUEST_CREATED
    update_count = len(client.updates)
    finalize_github_issue(invocation, client, max_comment_pages=5)
    assert len(client.updates) == update_count


@pytest.mark.parametrize(
    ("error", "category"),
    [
        (AgentRuntimeError("failure"), "agent_runtime"),
        (GitHubPublicationError("failure"), "publication"),
        (RuntimeError("failure"), "controller_failure"),
    ],
)
def test_failure_classification_is_bounded(error: Exception, category: str) -> None:
    assert classify_github_failure(error) == category


def _run(
    invocation: GitHubInvocation,
    client: FakeClient,
    tmp_path: Path,
    checkout: Path,
    *,
    solve_runner,
    settings_factory=None,
    orchestrator_factory=None,
    publisher=None,
):
    import asyncio

    return asyncio.run(
        run_github_issue(
            invocation,
            client,
            GitHubSettings(github_token="github-secret"),
            target_checkout=checkout,
            context_dir=tmp_path / "context",
            diagnostics_dir=tmp_path / "diagnostics",
            runner_temp=tmp_path / "runner",
            status_comment_id=client.status_comment_id,
            settings_factory=settings_factory or (lambda: _settings(tmp_path)),
            orchestrator_factory=orchestrator_factory or (lambda settings: object()),
            solve_runner=solve_runner,
            publisher=publisher or (
                lambda *args, **kwargs: pytest.fail("unexpected publisher call")
            ),
        )
    )


def _checkout(tmp_path: Path) -> tuple[Path, str]:
    checkout = tmp_path / "target"
    checkout.mkdir()
    _git(checkout, "init", "-b", "main")
    _git(checkout, "config", "user.name", "Test User")
    _git(checkout, "config", "user.email", "test@example.com")
    (checkout / "app.py").write_text("value = 1\n", encoding="utf-8")
    _git(checkout, "add", "app.py")
    _git(checkout, "commit", "-m", "initial")
    return checkout.resolve(), _git_output(checkout, "rev-parse", "HEAD")


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        openai_api_key="model-secret",
        runs_dir=tmp_path / "runs",
        command_timeout_seconds=17,
    )


def _solve_result(
    tmp_path: Path,
    base_sha: str,
    *,
    diff: str,
    changed_files: list[str],
) -> SolveResult:
    run_dir = tmp_path / "run"
    workspace = run_dir / "repo"
    workspace.mkdir(parents=True, exist_ok=True)
    (run_dir / "metadata.json").write_text('{"run_id":"run-id"}\n', encoding="utf-8")
    (run_dir / "agent-final.json").write_text('{"summary":"done"}\n', encoding="utf-8")
    (run_dir / "changed-files.json").write_text(
        json.dumps(changed_files), encoding="utf-8"
    )
    (run_dir / "diff.patch").write_text(diff, encoding="utf-8")
    return SolveResult(
        run_id="run-id",
        base_sha=base_sha,
        summary="Completed @all <!-- sage-state:failed -->",
        remaining_uncertainty=["Check @team"],
        changed_files=changed_files,
        diff=diff,
        run_dir=run_dir,
        workspace_dir=workspace,
    )


def _invocation(base_sha: str) -> GitHubInvocation:
    return load_issue_comment_event(
        {
            "GITHUB_EVENT_NAME": "issue_comment",
            "GITHUB_EVENT_PATH": str(FIXTURES / "issue_solve.json"),
            "GITHUB_REPOSITORY": "24aysh/example",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_RUN_ID": "9001",
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_SHA": base_sha,
        }
    )


def _git(repository: Path, *arguments: str) -> None:
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def _git_output(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()
