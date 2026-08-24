"""Offline exercise of the production GitHub publication boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sage.domain.results import SolveResult
from sage.errors import GitHubPublicationError
from sage.integrations.github.api_models import GitHubPullRequestSnapshot
from sage.integrations.github.commands import SageCommand
from sage.integrations.github.models import (
    GitHubActionsRun,
    GitHubActor,
    GitHubComment,
    GitHubInvocation,
    GitHubIssue,
    GitHubRepository,
)
from sage.integrations.github.publishing import (
    PublicationResult,
    publish_solve_result,
)
from sage.repository.host_git import run_git
from sage.repository.patch import normalize_null_file_headers
from sage.repository.selection import IGNORED_UNTRACKED_PATHSPECS


@dataclass(frozen=True, slots=True)
class LocalPublicationSmokeResult:
    """Inspectable output from one model-free publication simulation."""

    output_dir: Path
    candidate_dir: Path
    remote_dir: Path
    base_sha: str
    default_branch_sha: str
    sage_branch_sha: str
    publication: PublicationResult
    pull_request_title: str
    pull_request_body: str
    pull_request_draft: bool


@dataclass(slots=True)
class _LocalGitHubClient:
    """Record the PR request while Git itself simulates branch publication."""

    title: str = ""
    body: str = ""
    draft: bool = False
    created: bool = False

    def list_open_pull_requests(self, repository, *, head_branch, base_branch):
        del repository, head_branch, base_branch
        return ()

    def get_branch(self, repository, branch):
        del repository, branch
        return None

    def create_pull_request(
        self,
        repository,
        *,
        title,
        head_branch,
        base_branch,
        body,
        draft,
    ):
        del repository
        self.title = title
        self.body = body
        self.draft = draft
        self.created = True
        return GitHubPullRequestSnapshot(
            number=1,
            html_url="https://github.com/sage-local/publication-smoke/pull/1",
            state="open",
            draft=draft,
            head_ref=head_branch,
            base_ref=base_branch,
        )


def default_publication_smoke_dir(root: Path) -> Path:
    """Return a collision-resistant retained output path beneath ``.sage``."""

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return root.expanduser().resolve() / ".sage" / "publication-smoke" / (
        f"{stamp}-{uuid4().hex[:8]}"
    )


def run_publication_smoke(
    output_dir: Path,
    *,
    repository: Path | None = None,
    patch_file: Path | None = None,
    base_ref: str = "HEAD",
    issue_number: int = 17,
) -> LocalPublicationSmokeResult:
    """Publish a fixture or supplied patch to a local bare Git remote.

    This deliberately calls the production publisher but replaces GitHub's REST
    API with an in-memory recorder. It performs no model or network calls.
    """

    if (repository is None) != (patch_file is None):
        raise GitHubPublicationError(
            "--repo and --patch-file must be provided together."
        )
    if issue_number < 1:
        raise GitHubPublicationError("The smoke-test issue number must be positive.")

    root = output_dir.expanduser().resolve()
    if root.exists():
        raise GitHubPublicationError(
            f"Publication smoke output already exists: {root}"
        )
    try:
        root.mkdir(parents=True)
    except OSError as error:
        raise GitHubPublicationError(
            f"Unable to create publication smoke output: {root}"
        ) from error

    remote = root / "remote.git"
    candidate = root / "candidate"
    runner_temp = root / "runner-temp"
    if repository is None:
        base_sha = _prepare_fixture(root, remote=remote, candidate=candidate)
    else:
        assert patch_file is not None
        base_sha = _prepare_supplied_candidate(
            repository.expanduser().resolve(),
            patch_file.expanduser().resolve(),
            base_ref=base_ref,
            remote=remote,
            candidate=candidate,
        )

    result = _solve_result(candidate, base_sha, root)
    invocation = _invocation(base_sha, issue_number=issue_number)
    client = _LocalGitHubClient()
    publication = publish_solve_result(
        invocation,
        result,
        client,
        github_token="local-publication-smoke-token",
        runner_temp=runner_temp,
        remote_url_factory=lambda _: str(remote),
    )
    branch_ref = f"refs/heads/{publication.branch_name}"
    default_sha = _git_output(
        ["rev-parse", "refs/heads/main"],
        repository=remote,
    ).strip()
    branch_sha = _git_output(["rev-parse", branch_ref], repository=remote).strip()
    if default_sha != base_sha or branch_sha == base_sha or not client.created:
        raise GitHubPublicationError(
            "The local publication simulation violated a publication invariant."
        )
    if not client.draft:
        raise GitHubPublicationError(
            "The local publication simulation did not request a draft Pull Request."
        )
    return LocalPublicationSmokeResult(
        output_dir=root,
        candidate_dir=candidate,
        remote_dir=remote,
        base_sha=base_sha,
        default_branch_sha=default_sha,
        sage_branch_sha=branch_sha,
        publication=publication,
        pull_request_title=client.title,
        pull_request_body=client.body,
        pull_request_draft=client.draft,
    )


def _prepare_fixture(root: Path, *, remote: Path, candidate: Path) -> str:
    seed = root / "seed"
    _git_required(["init", "--bare", str(remote)])
    _git_required(["init", "-b", "main", str(seed)])
    _git_required(["config", "user.name", "Sage Smoke Test"], repository=seed)
    _git_required(
        ["config", "user.email", "sage-smoke@example.invalid"],
        repository=seed,
    )
    (seed / "app.py").write_text("value = 1\n", encoding="utf-8")
    _git_required(["add", "app.py"], repository=seed)
    _git_required(["commit", "-m", "initial fixture"], repository=seed)
    _git_required(["push", str(remote), "main:main"], repository=seed)
    base_sha = _git_output(["rev-parse", "HEAD"], repository=seed).strip()
    _git_required(["clone", "--no-checkout", str(remote), str(candidate)])
    _git_required(["checkout", "--detach", base_sha], repository=candidate)
    (candidate / "app.py").write_text("value = 2\n", encoding="utf-8")
    return base_sha


def _prepare_supplied_candidate(
    repository: Path,
    patch_file: Path,
    *,
    base_ref: str,
    remote: Path,
    candidate: Path,
) -> str:
    if not repository.is_dir() or not (repository / ".git").exists():
        raise GitHubPublicationError(
            f"Publication smoke repository is not a Git checkout: {repository}"
        )
    if not patch_file.is_file():
        raise GitHubPublicationError(
            f"Publication smoke patch does not exist: {patch_file}"
        )
    try:
        patch = normalize_null_file_headers(patch_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as error:
        raise GitHubPublicationError(
            f"Unable to read publication smoke patch: {patch_file}"
        ) from error
    if not patch.strip():
        raise GitHubPublicationError("Publication smoke patch cannot be empty.")

    _git_required(["init", "--bare", str(remote)])
    _git_required(["clone", "--no-checkout", str(repository), str(candidate)])
    _git_required(["checkout", "--detach", base_ref], repository=candidate)
    base_sha = _git_output(["rev-parse", "HEAD"], repository=candidate).strip()
    _git_required(
        ["push", str(remote), f"{base_sha}:refs/heads/main"],
        repository=candidate,
    )
    _git_required(
        ["apply", "--whitespace=fix", "--recount", "-"],
        repository=candidate,
        input_text=patch,
        failure="Unable to apply the supplied patch in the publication smoke checkout.",
    )
    return base_sha


def _solve_result(candidate: Path, base_sha: str, run_dir: Path) -> SolveResult:
    _git_required(
        [
            "add",
            "--intent-to-add",
            "--all",
            "--",
            ".",
            *IGNORED_UNTRACKED_PATHSPECS,
        ],
        repository=candidate,
    )
    changed_files = sorted(
        path
        for path in _git_output(
            ["diff", "--name-only", "-z", "--no-ext-diff", "HEAD", "--"],
            repository=candidate,
        ).split("\0")
        if path
    )
    diff = _git_output(
        ["diff", "--binary", "--no-ext-diff", "HEAD", "--"],
        repository=candidate,
    )
    if not changed_files or not diff.strip():
        raise GitHubPublicationError(
            "The publication smoke candidate did not produce a Git diff."
        )
    return SolveResult(
        run_id="local-publication-smoke",
        base_sha=base_sha,
        summary="Offline publication smoke candidate.",
        remaining_uncertainty=[],
        changed_files=changed_files,
        diff=diff,
        run_dir=run_dir,
        workspace_dir=candidate,
    )


def _invocation(base_sha: str, *, issue_number: int) -> GitHubInvocation:
    repository = GitHubRepository(
        owner="sage-local",
        name="publication-smoke",
        repository_id=1,
        html_url="https://github.com/sage-local/publication-smoke",
    )
    issue_url = f"{repository.html_url}/issues/{issue_number}"
    return GitHubInvocation(
        repository=repository,
        issue=GitHubIssue(
            number=issue_number,
            title="Offline publication smoke test",
            body="Exercise the deterministic publisher without network calls.",
            html_url=issue_url,
        ),
        actor=GitHubActor(login="sage-local", user_id=1),
        comment=GitHubComment(
            comment_id=1,
            body="/sage solve",
            created_at=datetime.now(UTC),
            html_url=f"{issue_url}#issuecomment-1",
        ),
        command=SageCommand.SOLVE,
        default_branch="main",
        base_sha=base_sha,
        actions_run=GitHubActionsRun(
            run_id=1,
            attempt=1,
            html_url=f"{repository.html_url}/actions/runs/1",
        ),
    )


def _git_required(
    arguments: list[str],
    *,
    repository: Path | None = None,
    input_text: str | None = None,
    failure: str = "Unable to prepare the local publication simulation.",
) -> None:
    result = run_git(arguments, repository=repository, input_text=input_text)
    if result.returncode != 0:
        detail = " ".join((result.stderr or result.stdout).split())[:1_000]
        raise GitHubPublicationError(f"{failure} {detail}".strip())


def _git_output(arguments: list[str], *, repository: Path) -> str:
    result = run_git(arguments, repository=repository)
    if result.returncode != 0:
        detail = " ".join((result.stderr or result.stdout).split())[:1_000]
        raise GitHubPublicationError(
            f"Unable to inspect the local publication simulation. {detail}".strip()
        )
    return result.stdout
