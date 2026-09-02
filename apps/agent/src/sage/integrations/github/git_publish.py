"""Credential-scoped trusted Git transaction for GitHub publication."""

from __future__ import annotations

import os
import re
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from subprocess import CompletedProcess

from sage.domain.solve import SolveResult
from sage.errors import GitHubPublicationError, HostGitError
from sage.integrations.github.models import GIT_OBJECT_ID_PATTERN, GitHubInvocation
from sage.repository.host_git import run_git
from sage.repository.output import truncate_text
from sage.repository.selection import IGNORED_UNTRACKED_PATHSPECS

_CONTROLLER_BASE_REF = "refs/sage/controller/default-branch"
_SAFE_LOCAL_CONFIG_KEYS = frozenset(
    {
        "core.bare",
        "core.filemode",
        "core.ignorecase",
        "core.logallrefupdates",
        "core.precomposeunicode",
        "core.repositoryformatversion",
        "core.symlinks",
        "remote.origin.fetch",
        "remote.origin.url",
    }
)
_SAFE_GIT_ARGUMENT_PREFIX = ("-c", "core.hooksPath=/dev/null")
_MAX_GIT_DIAGNOSTIC_CHARS = 4_000
_MAX_GIT_DIAGNOSTIC_LINES = 40


class BaseMovement(StrEnum):
    """Relationship between the event base and current default branch."""

    UNCHANGED = "unchanged"
    ADVANCED = "advanced"


@dataclass(frozen=True, slots=True)
class GitPublicationSession:
    """Validated local transaction retaining credentials until its push."""

    workspace: Path
    remote_url: str
    branch_name: str
    environment: Mapping[str, str]
    timeout_seconds: int
    current_base_sha: str
    base_movement: BaseMovement

    def push_creation_only(self) -> None:
        destination = f"refs/heads/{self.branch_name}"
        push = _git(
            [
                "push",
                "--porcelain",
                f"--force-with-lease={destination}:",
                self.remote_url,
                f"HEAD:{destination}",
            ],
            repository=self.workspace,
            environment=self.environment,
            timeout_seconds=self.timeout_seconds,
        )
        if push.returncode != 0:
            raise GitHubPublicationError(
                "GitHub rejected the creation-only Sage branch push."
            )


@contextmanager
def prepare_git_publication(
    invocation: GitHubInvocation,
    result: SolveResult,
    *,
    github_token: str,
    runner_temp: Path,
    remote_url: str,
    branch_name: str,
    timeout_seconds: int,
) -> Iterator[GitPublicationSession]:
    """Validate and commit a candidate while scoping temporary credentials."""

    workspace = result.workspace_dir.expanduser().resolve()
    _validate_workspace(workspace)
    _validate_local_config(
        workspace,
        github_token=github_token,
        timeout_seconds=timeout_seconds,
    )
    _validate_authoritative_candidate(
        result,
        workspace=workspace,
        timeout_seconds=timeout_seconds,
    )
    resolved_runner_temp = runner_temp.expanduser().resolve()
    if resolved_runner_temp == workspace or workspace in resolved_runner_temp.parents:
        raise GitHubPublicationError(
            "The runner temporary directory must be outside the candidate workspace."
        )
    askpass_path = _create_askpass(resolved_runner_temp)
    environment = _git_environment(
        askpass_path=askpass_path,
        github_token=github_token,
    )
    try:
        current_base_sha, base_movement = _fetch_and_classify_base(
            invocation,
            workspace=workspace,
            remote_url=remote_url,
            environment=environment,
            timeout_seconds=timeout_seconds,
        )
        _create_commit(
            invocation,
            result,
            workspace=workspace,
            branch_name=branch_name,
            timeout_seconds=timeout_seconds,
        )
        yield GitPublicationSession(
            workspace=workspace,
            remote_url=remote_url,
            branch_name=branch_name,
            environment=environment,
            timeout_seconds=timeout_seconds,
            current_base_sha=current_base_sha,
            base_movement=base_movement,
        )
    finally:
        askpass_path.unlink(missing_ok=True)


def _validate_workspace(workspace: Path) -> None:
    git_directory = workspace / ".git"
    if not workspace.is_dir() or not git_directory.is_dir() or git_directory.is_symlink():
        raise GitHubPublicationError(
            "The authoritative solve workspace is not a safe Git checkout."
        )


def _validate_local_config(
    workspace: Path,
    *,
    github_token: str,
    timeout_seconds: int,
) -> None:
    config = _required_git(
        ["config", "--local", "--name-only", "--null", "--list"],
        repository=workspace,
        timeout_seconds=timeout_seconds,
    )
    keys = {key.lower() for key in config.stdout.split("\0") if key}
    unexpected = {
        key
        for key in keys - _SAFE_LOCAL_CONFIG_KEYS
        if re.fullmatch(r"branch\.[^.]+\.(?:merge|remote)", key) is None
    }
    if unexpected:
        raise GitHubPublicationError(
            "The candidate Git configuration contains unsupported local settings."
        )
    try:
        config_body = (workspace / ".git" / "config").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise GitHubPublicationError(
            "The candidate Git configuration could not be validated."
        ) from error
    if github_token in config_body:
        raise GitHubPublicationError(
            "GitHub publication credentials must not be stored in Git config."
        )


def _validate_authoritative_candidate(
    result: SolveResult,
    *,
    workspace: Path,
    timeout_seconds: int,
) -> None:
    head = _required_git(
        ["rev-parse", "--verify", "HEAD"],
        repository=workspace,
        timeout_seconds=timeout_seconds,
    ).stdout.strip()
    if head != result.base_sha:
        raise GitHubPublicationError(
            "The candidate HEAD no longer matches the authoritative solve base."
        )
    _required_git(
        ["add", "--intent-to-add", "--all", "--", ".", *IGNORED_UNTRACKED_PATHSPECS],
        repository=workspace,
        timeout_seconds=timeout_seconds,
    )
    _required_git(
        ["diff", "--check", "HEAD", "--"],
        repository=workspace,
        timeout_seconds=timeout_seconds,
        failure="The candidate diff failed Git whitespace validation.",
    )
    candidate_diff = _required_git(
        ["diff", "--binary", "--no-ext-diff", "HEAD", "--"],
        repository=workspace,
        timeout_seconds=timeout_seconds,
    ).stdout
    if candidate_diff != result.diff:
        raise GitHubPublicationError(
            "The candidate diff changed after the authoritative solve result."
        )
    candidate_paths = _changed_paths(
        ["diff", "--name-only", "-z", "--no-ext-diff", "HEAD", "--"],
        repository=workspace,
        timeout_seconds=timeout_seconds,
    )
    if candidate_paths != set(result.changed_files):
        raise GitHubPublicationError(
            "The candidate paths do not match the authoritative solve result."
        )


def _fetch_and_classify_base(
    invocation: GitHubInvocation,
    *,
    workspace: Path,
    remote_url: str,
    environment: Mapping[str, str],
    timeout_seconds: int,
) -> tuple[str, BaseMovement]:
    _required_git(
        [
            "fetch",
            "--force",
            "--no-tags",
            "--no-recurse-submodules",
            remote_url,
            f"refs/heads/{invocation.default_branch}:{_CONTROLLER_BASE_REF}",
        ],
        repository=workspace,
        environment=environment,
        timeout_seconds=timeout_seconds,
        failure="Unable to fetch the current GitHub default branch safely.",
    )
    current = _required_git(
        ["rev-parse", "--verify", _CONTROLLER_BASE_REF],
        repository=workspace,
        timeout_seconds=timeout_seconds,
    ).stdout.strip()
    if re.fullmatch(GIT_OBJECT_ID_PATTERN, current) is None:
        raise GitHubPublicationError(
            "The current GitHub default-branch commit is invalid."
        )
    if current == invocation.base_sha:
        return current, BaseMovement.UNCHANGED
    ancestry = _git(
        ["merge-base", "--is-ancestor", invocation.base_sha, current],
        repository=workspace,
        timeout_seconds=timeout_seconds,
    )
    if ancestry.returncode == 0:
        return current, BaseMovement.ADVANCED
    if ancestry.returncode == 1:
        raise GitHubPublicationError(
            "The default branch was rewritten after this solve began; retry "
            "against a new exact command event."
        )
    raise GitHubPublicationError(
        "Unable to verify the current default-branch ancestry."
    )


def _create_commit(
    invocation: GitHubInvocation,
    result: SolveResult,
    *,
    workspace: Path,
    branch_name: str,
    timeout_seconds: int,
) -> None:
    for key, value in (
        ("core.hooksPath", "/dev/null"),
        ("commit.gpgSign", "false"),
        ("user.name", "Sage GitHub Actions"),
        ("user.email", "41898282+github-actions[bot]@users.noreply.github.com"),
    ):
        _required_git(
            ["config", "--local", key, value],
            repository=workspace,
            timeout_seconds=timeout_seconds,
        )
    _required_git(
        ["checkout", "-b", branch_name, invocation.base_sha],
        repository=workspace,
        timeout_seconds=timeout_seconds,
        failure="Unable to create the deterministic local publication branch.",
    )
    _required_git(
        ["reset", "--mixed", "HEAD", "--"],
        repository=workspace,
        timeout_seconds=timeout_seconds,
        failure="Unable to reset the candidate index before publication.",
    )
    _required_git(
        ["apply", "--cached", "--binary", "-"],
        repository=workspace,
        timeout_seconds=timeout_seconds,
        input_text=result.diff,
        failure="Unable to stage the authoritative candidate.",
    )
    _required_git(
        ["diff", "--cached", "--check", "HEAD", "--"],
        repository=workspace,
        timeout_seconds=timeout_seconds,
        failure="The staged candidate failed Git whitespace validation.",
    )
    staged_paths = _changed_paths(
        ["diff", "--cached", "--name-only", "-z", "--no-ext-diff", "HEAD", "--"],
        repository=workspace,
        timeout_seconds=timeout_seconds,
    )
    if staged_paths != set(result.changed_files):
        raise GitHubPublicationError(
            "The staged paths do not match the authoritative solve result."
        )
    staged_diff = _required_git(
        ["diff", "--cached", "--binary", "--no-ext-diff", "HEAD", "--"],
        repository=workspace,
        timeout_seconds=timeout_seconds,
    ).stdout
    if staged_diff != result.diff:
        raise GitHubPublicationError(
            "The staged diff does not match the authoritative solve result."
        )
    _required_git(
        ["commit", "--no-verify", "--message", f"fix: resolve issue #{invocation.issue.number}"],
        repository=workspace,
        timeout_seconds=timeout_seconds,
        failure="Unable to create the deterministic Sage commit.",
    )


def _create_askpass(runner_temp: Path) -> Path:
    directory = runner_temp.expanduser().resolve() / "sage-credentials"
    try:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
        path = directory / "git-askpass.sh"
        path.write_text(
            "#!/bin/sh\n"
            "case \"$1\" in\n"
            "  *Username*) printf '%s\\n' 'x-access-token' ;;\n"
            "  *) printf '%s\\n' \"$SAGE_GIT_TOKEN\" ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        path.chmod(0o700)
    except OSError as error:
        raise GitHubPublicationError(
            "Unable to create the temporary Git credential helper."
        ) from error
    return path


def _git_environment(*, askpass_path: Path, github_token: str) -> dict[str, str]:
    environment = _base_git_environment()
    environment.update(
        {
            "GIT_ASKPASS": str(askpass_path),
            "GIT_ASKPASS_REQUIRE": "force",
            "SAGE_GIT_TOKEN": github_token,
        }
    )
    return environment


def _base_git_environment() -> dict[str, str]:
    environment = {
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "LC_ALL": "C",
    }
    path = os.environ.get("PATH")
    if path:
        environment["PATH"] = path
    return environment


def _changed_paths(
    arguments: Sequence[str],
    *,
    repository: Path,
    timeout_seconds: int,
) -> set[str]:
    output = _required_git(
        arguments,
        repository=repository,
        timeout_seconds=timeout_seconds,
    ).stdout
    return {path for path in output.split("\0") if path}


def _required_git(
    arguments: Sequence[str],
    *,
    repository: Path,
    timeout_seconds: int,
    environment: Mapping[str, str] | None = None,
    input_text: str | None = None,
    failure: str = "A required trusted Git operation failed.",
) -> CompletedProcess[str]:
    result = _git(
        arguments,
        repository=repository,
        timeout_seconds=timeout_seconds,
        environment=environment,
        input_text=input_text,
    )
    if result.returncode != 0:
        diagnostics = _git_failure_diagnostics(result)
        message = failure
        if diagnostics:
            message = (
                f"{failure}\nGit exited with code {result.returncode}. Diagnostics:\n"
                f"{diagnostics}"
            )
        raise GitHubPublicationError(message)
    return result


def _git_failure_diagnostics(result: CompletedProcess[str]) -> str:
    sections: list[str] = []
    for label, stream in (("stderr", result.stderr), ("stdout", result.stdout)):
        lines = _safe_git_lines(stream)
        if lines:
            sections.append(f"Git {label}:\n" + "\n".join(lines))
    if not sections:
        return ""
    bounded = truncate_text(
        "\n".join(sections),
        _MAX_GIT_DIAGNOSTIC_CHARS - (2 * _MAX_GIT_DIAGNOSTIC_LINES),
    )
    return "\n".join(f"  {line}" for line in bounded.splitlines())


def _safe_git_lines(value: str) -> list[str]:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    safe = "".join(
        character
        if character in "\n\t" or (ord(character) >= 32 and ord(character) != 127)
        else "�"
        for character in normalized
    ).strip("\n")
    if not safe:
        return []
    lines = safe.split("\n")
    if len(lines) <= _MAX_GIT_DIAGNOSTIC_LINES:
        return lines
    head_count = _MAX_GIT_DIAGNOSTIC_LINES // 2
    tail_count = _MAX_GIT_DIAGNOSTIC_LINES - head_count - 1
    omitted = len(lines) - head_count - tail_count
    return [
        *lines[:head_count],
        f"... [{omitted} diagnostic lines omitted] ...",
        *lines[-tail_count:],
    ]


def _git(
    arguments: Sequence[str],
    *,
    repository: Path,
    timeout_seconds: int,
    environment: Mapping[str, str] | None = None,
    input_text: str | None = None,
) -> CompletedProcess[str]:
    try:
        return run_git(
            [*_SAFE_GIT_ARGUMENT_PREFIX, *arguments],
            repository=repository,
            timeout_seconds=timeout_seconds,
            environment=environment or _base_git_environment(),
            input_text=input_text,
        )
    except HostGitError as error:
        raise GitHubPublicationError(
            "A required trusted Git operation could not be executed."
        ) from error
