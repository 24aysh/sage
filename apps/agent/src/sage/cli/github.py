"""Trusted GitHub command arguments and controller dispatch."""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from sage.composition import build_retrieval_service, build_orchestrator
from sage.config import Settings
from sage.errors import GitHubConfigurationError
from sage.integrations.github.client import RestGitHubClient
from sage.integrations.github.config import GitHubSettings
from sage.integrations.github.events import load_issue_comment_event, load_issue_comment_fixture
from sage.integrations.github.gate import evaluate_gate
from sage.integrations.github.outputs import write_gate_outputs
from sage.integrations.github.publication_smoke import default_publication_smoke_dir, run_publication_smoke
from sage.workflows.github import finalize_github_issue, run_github_issue


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    github_parser = subparsers.add_parser("github", help="Run trusted GitHub Actions controller commands.")
    github_subparsers = github_parser.add_subparsers(
        dest="github_command",
        required=True,
    )
    gate_parser = github_subparsers.add_parser("gate", help="Authorize and deduplicate one issue-comment invocation.")
    gate_parser.add_argument(
        "--event-file",
        type=Path,
        help="Override GITHUB_EVENT_PATH for deterministic local testing.",
    )
    gate_parser.add_argument(
        "--output-file",
        type=Path,
        help="Override GITHUB_OUTPUT for deterministic local testing.",
    )
    gate_parser.add_argument("--debug", action="store_true")
    gate_parser.set_defaults(handler=_run_github_gate)

    solve_github_parser = github_subparsers.add_parser("solve", help="Run one accepted GitHub Issue solve and publication lifecycle.")
    solve_github_parser.add_argument("--event-file", type=Path)
    solve_github_parser.add_argument("--target-checkout", required=True, type=Path)
    solve_github_parser.add_argument("--context-dir", required=True, type=Path)
    solve_github_parser.add_argument("--diagnostics-dir", required=True, type=Path)
    solve_github_parser.add_argument("--runner-temp", required=True, type=Path)
    solve_github_parser.add_argument(
        "--status-comment-id",
        required=True,
        type=_positive_integer,
    )
    solve_github_parser.add_argument("--debug", action="store_true")
    solve_github_parser.set_defaults(handler=_run_github_solve)

    finalize_parser = github_subparsers.add_parser("finalize", help="Repair a non-terminal GitHub invocation status safely.")
    finalize_parser.add_argument("--event-file", type=Path)
    finalize_parser.add_argument("--debug", action="store_true")
    finalize_parser.set_defaults(handler=_run_github_finalize)

    event_parser = github_subparsers.add_parser("event-check", help="Classify one local event fixture without GitHub or a model.")
    event_parser.add_argument("--event-file", required=True, type=Path)
    event_parser.add_argument("--debug", action="store_true")
    event_parser.set_defaults(handler=_run_github_event_check)

    publication_smoke_parser = github_subparsers.add_parser("publication-smoke", help="Exercise branch and draft-PR publication entirely offline.")
    publication_smoke_parser.add_argument("--output-dir", type=Path)
    publication_smoke_parser.add_argument("--repo", type=Path)
    publication_smoke_parser.add_argument("--patch-file", type=Path)
    publication_smoke_parser.add_argument("--base-ref", default="HEAD")
    publication_smoke_parser.add_argument(
        "--issue-number",
        default=17,
        type=_positive_integer,
    )
    publication_smoke_parser.add_argument("--debug", action="store_true")
    publication_smoke_parser.set_defaults(handler=_run_github_publication_smoke)


def _run_github_gate(arguments: argparse.Namespace) -> int:
    """Run the model-free GitHub command gate and emit safe job outputs."""

    environment = _github_environment(arguments.event_file)
    output_path = arguments.output_file
    if output_path is None:
        raw_output_path = environment.get("GITHUB_OUTPUT", "").strip()
        if not raw_output_path:
            raise GitHubConfigurationError(
                "GITHUB_OUTPUT or --output-file is required for the GitHub gate."
            )
        output_path = Path(raw_output_path)

    settings = GitHubSettings.from_env(environment)
    invocation = load_issue_comment_event(environment)
    client = RestGitHubClient(settings)
    result = evaluate_gate(
        invocation,
        client,
        max_comment_pages=settings.max_comment_pages,
    )
    write_gate_outputs(result, output_path.expanduser())
    print(f"GitHub gate outcome: {result.outcome.value}")
    return 0


def _run_github_solve(arguments: argparse.Namespace) -> int:
    """Run the trusted GitHub solve controller."""

    environment = _github_environment(arguments.event_file)
    github_settings = GitHubSettings.from_env(environment)
    invocation = load_issue_comment_event(environment)
    client = RestGitHubClient(github_settings)
    result = asyncio.run(
        run_github_issue(
            invocation,
            client,
            github_settings,
            target_checkout=arguments.target_checkout,
            context_dir=arguments.context_dir,
            diagnostics_dir=arguments.diagnostics_dir,
            runner_temp=arguments.runner_temp,
            status_comment_id=arguments.status_comment_id,
            orchestrator_factory=build_orchestrator,
            settings_factory=lambda: Settings.from_env(environment),
            retrieval_service_factory=build_retrieval_service,
        )
    )
    print(f"GitHub solve outcome: {result.outcome.value}")
    return 0


def _run_github_finalize(arguments: argparse.Namespace) -> int:
    """Repair an interrupted invocation without loading model configuration."""

    environment = _github_environment(arguments.event_file)
    settings = GitHubSettings.from_env(environment)
    invocation = load_issue_comment_event(environment)
    client = RestGitHubClient(settings)
    finalize_github_issue(
        invocation,
        client,
        max_comment_pages=settings.max_comment_pages,
    )
    print("GitHub finalizer completed.")
    return 0


def _run_github_event_check(arguments: argparse.Namespace) -> int:
    """Classify a fixture through the production event parser offline."""

    invocation = load_issue_comment_fixture(arguments.event_file)
    if invocation.issue.is_pull_request:
        classification = "ignored_pull_request_comment"
    elif invocation.command is None:
        classification = "ignored_ordinary_comment"
    else:
        classification = f"supported_{invocation.command.value}"
    print(f"GitHub event classification: {classification}")
    return 0


def _run_github_publication_smoke(arguments: argparse.Namespace) -> int:
    """Exercise production Git publication with local deterministic substitutes."""

    output_dir = arguments.output_dir or default_publication_smoke_dir(Path.cwd())
    result = run_publication_smoke(
        output_dir,
        repository=arguments.repo,
        patch_file=arguments.patch_file,
        base_ref=arguments.base_ref,
        issue_number=arguments.issue_number,
    )
    print("GitHub publication smoke: passed")
    print(f"  Output: {result.output_dir}")
    print(f"  Default branch: main @ {result.default_branch_sha[:12]} (unchanged)")
    print(
        f"  Sage branch: {result.publication.branch_name} "
        f"@ {result.sage_branch_sha[:12]}"
    )
    print(f"  Commit: fix: resolve issue #{arguments.issue_number}")
    print(f"  Draft PR requested: {str(result.pull_request_draft).lower()}")
    print(f"  PR title: {result.pull_request_title}")
    print("  Model calls: 0")
    print("  Network calls: 0")
    return 0


def _github_environment(event_file: Path | None) -> dict[str, str]:
    environment = dict(os.environ)
    if event_file is not None:
        environment["GITHUB_EVENT_PATH"] = str(event_file.expanduser().resolve())
    return environment


def _positive_integer(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("expected a positive integer") from error
    if parsed < 1 or str(parsed) != value:
        raise argparse.ArgumentTypeError("expected a positive integer")
    return parsed
