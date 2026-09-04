"""Command-line entrypoints for local solves and trusted automation."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from langchain_core.tracers.langchain import wait_for_all_tracers

from sage.composition import build_legion_memory_service, build_orchestrator
from sage.config import Settings
from sage.domain.memory import MemoryRetrievalResult, MemoryRetrievalStatus
from sage.domain.solve import SolveOutcome, SolveRequest, SolveResult
from sage.errors import (
    ConfigurationError,
    GitHubConfigurationError,
    LegionMemoryQueryError,
    SageError,
)
from sage.integrations.github.client import RestGitHubClient
from sage.integrations.github.config import GitHubSettings
from sage.integrations.github.events import (
    load_issue_comment_event,
    load_issue_comment_fixture,
)
from sage.integrations.github.gate import evaluate_gate
from sage.integrations.github.outputs import write_gate_outputs
from sage.integrations.github.publication_smoke import (
    default_publication_smoke_dir,
    run_publication_smoke,
)
from sage.workflows.github import finalize_github_issue, run_github_issue
from sage.workflows.solve import solve_issue

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    """Run the Sage CLI and return a documented process exit code."""

    parser = _build_parser()
    arguments = parser.parse_args(argv)
    _configure_logging(debug=arguments.debug)

    try:
        handler: Callable[[argparse.Namespace], int] = arguments.handler
        return handler(arguments)
    except SageError as error:
        if arguments.debug:
            logger.exception("Sage failed")
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("ERROR: Interrupted.", file=sys.stderr)
        return 1
    except Exception as error:
        if arguments.debug:
            logger.exception("Unexpected Sage failure")
        print(f"ERROR: Unexpected failure: {error}", file=sys.stderr)
        return 1
    finally:
        _flush_langsmith_traces()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sage",
        description="Solve repository issues locally or through trusted automation.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    solve_parser = subparsers.add_parser(
        "solve",
        help="Run the issue solver.",
    )
    solve_parser.add_argument("--repo", required=True, type=Path)
    solve_parser.add_argument("--issue-file", required=True, type=Path)
    solve_parser.add_argument("--base-ref", default="HEAD")
    solve_parser.add_argument("--sandbox-image")
    solve_parser.add_argument("--debug", action="store_true")
    solve_parser.set_defaults(handler=_run_local_solve)

    memory_parser = subparsers.add_parser(
        "memory",
        help="Build or inspect the local Legion Memory graph.",
    )
    memory_subparsers = memory_parser.add_subparsers(
        dest="memory_command",
        required=True,
    )
    memory_build_parser = memory_subparsers.add_parser(
        "build",
        help="Build, update, or confirm a repository graph.",
    )
    memory_build_parser.add_argument("--repo", required=True, type=Path)
    memory_build_parser.add_argument("--memory-file", type=Path)
    memory_build_parser.add_argument("--full-rebuild", action="store_true")
    memory_build_parser.add_argument("--debug", action="store_true")
    memory_build_parser.set_defaults(handler=_run_memory_build)

    memory_status_parser = memory_subparsers.add_parser(
        "status",
        help="Inspect graph readiness and provenance.",
    )
    memory_status_parser.add_argument("--repo", required=True, type=Path)
    memory_status_parser.add_argument("--memory-file", type=Path)
    memory_status_parser.add_argument("--debug", action="store_true")
    memory_status_parser.set_defaults(handler=_run_memory_status)

    memory_retrieve_parser = memory_subparsers.add_parser(
        "retrieve",
        help="Retrieve Issue-relevant context from a ready graph.",
    )
    memory_retrieve_parser.add_argument("--repo", required=True, type=Path)
    memory_retrieve_parser.add_argument("--issue-file", required=True, type=Path)
    memory_retrieve_parser.add_argument("--memory-file", required=True, type=Path)
    memory_retrieve_parser.add_argument("--debug", action="store_true")
    memory_retrieve_parser.set_defaults(handler=_run_memory_retrieve)

    github_parser = subparsers.add_parser(
        "github",
        help="Run trusted GitHub Actions controller commands.",
    )
    github_subparsers = github_parser.add_subparsers(
        dest="github_command",
        required=True,
    )
    gate_parser = github_subparsers.add_parser(
        "gate",
        help="Authorize and deduplicate one issue-comment invocation.",
    )
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

    solve_github_parser = github_subparsers.add_parser(
        "solve",
        help="Run one accepted GitHub Issue solve and publication lifecycle.",
    )
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

    finalize_parser = github_subparsers.add_parser(
        "finalize",
        help="Repair a non-terminal GitHub invocation status safely.",
    )
    finalize_parser.add_argument("--event-file", type=Path)
    finalize_parser.add_argument("--debug", action="store_true")
    finalize_parser.set_defaults(handler=_run_github_finalize)

    event_parser = github_subparsers.add_parser(
        "event-check",
        help="Classify one local event fixture without GitHub or a model.",
    )
    event_parser.add_argument("--event-file", required=True, type=Path)
    event_parser.add_argument("--debug", action="store_true")
    event_parser.set_defaults(handler=_run_github_event_check)

    publication_smoke_parser = github_subparsers.add_parser(
        "publication-smoke",
        help="Exercise branch and draft-PR publication entirely offline.",
    )
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
    return parser


def _run_memory_build(arguments: argparse.Namespace) -> int:
    """Run the strict standalone graph build command."""

    result = build_legion_memory_service().build_or_update_graph_tool(
        repo_root=arguments.repo,
        memory_file=arguments.memory_file,
        full_rebuild=arguments.full_rebuild,
    )
    print("Legion Memory build: ready")
    print(f"  Memory file: {result.memory_file}")
    print(f"  Build type: {result.build_type.value}")
    print(f"  Indexed SHA: {result.indexed_sha}")
    print(f"  Files indexed: {result.files_indexed}")
    print(f"  Files parsed: {result.files_parsed}")
    print(f"  Files removed: {result.files_removed}")
    print(f"  Nodes: {result.total_nodes}")
    print(f"  Edges: {result.total_edges}")
    print(f"  Flows: {result.total_flows}")
    print(f"  Communities: {result.total_communities}")
    print(f"  Languages: {', '.join(result.languages) or 'none'}")
    print(f"  Duration: {result.duration_ms:.2f} ms")
    if result.warnings:
        print("  Warnings:")
        for warning in result.warnings:
            print(f"    - {warning}")
    return 0


def _run_memory_status(arguments: argparse.Namespace) -> int:
    """Print a bounded graph health and provenance summary."""

    stats = build_legion_memory_service().graph_stats(
        repo_root=arguments.repo,
        memory_file=arguments.memory_file,
    )
    print(f"Legion Memory status: {stats.status.value}")
    print(f"  Memory file: {stats.memory_file}")
    if stats.status.value != "ready":
        print("  Build the graph with: sage memory build --repo <repository>")
        return 1
    print(f"  Build type: {stats.build_type.value if stats.build_type else 'unknown'}")
    print(f"  Indexed SHA: {stats.indexed_sha}")
    print(f"  Files: {stats.files}")
    print(f"  Nodes: {stats.nodes}")
    print(f"  Edges: {stats.edges}")
    print(f"  Flows: {stats.flows}")
    print(f"  Communities: {stats.communities}")
    print(f"  Languages: {', '.join(stats.languages) or 'none'}")
    print(f"  Last updated: {stats.last_updated}")
    return 0


def _run_memory_retrieve(arguments: argparse.Namespace) -> int:
    """Print an explainable, model-free retrieval result for one Issue."""

    issue_file = arguments.issue_file.expanduser().resolve()
    if not issue_file.is_file():
        raise LegionMemoryQueryError(f"Issue file does not exist: {issue_file}")
    try:
        issue_text = issue_file.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise LegionMemoryQueryError(
            f"Unable to read Issue file: {type(error).__name__}: {str(error)[:300]}"
        ) from error
    result = build_legion_memory_service().retrieve_issue_context(
        issue_text=issue_text,
        repo_root=arguments.repo,
        memory_file=arguments.memory_file,
    )
    _render_memory_retrieval(result)
    return 1 if result.status is MemoryRetrievalStatus.UNAVAILABLE else 0


def _render_memory_retrieval(result: MemoryRetrievalResult) -> None:
    """Render stable retrieval logs without trusting database text as terminal data."""

    print(f"Legion Memory retrieval: {result.status.value}")
    print(f"  Memory used: {'yes' if result.status is MemoryRetrievalStatus.USED else 'no'}")
    print(f"  Outcome: {result.outcome.value}")
    print(f"  Summary: {_safe_log_value(result.summary, 500)}")
    print(f"  Memory file: {result.memory_file}")
    print(f"  Indexed SHA: {result.indexed_sha or 'unavailable'}")
    print(f"  Search modes: {', '.join(result.search_modes) or 'none'}")
    print(
        "  Query terms: "
        + (", ".join(_safe_log_value(term, 80) for term in result.query_terms) or "none")
    )
    print(f"  Lexical candidates: {result.lexical_candidates}")
    print(f"  Graph-expanded candidates: {result.expanded_candidates}")
    print(f"  Retrieved: {result.returned}/{result.total_candidates}")
    print(f"  Omitted: {result.omitted}")
    print(f"  Truncated: {'yes' if result.truncated else 'no'}")
    print(f"  Context characters: {result.context_chars}")
    print(f"  Duration: {result.duration_ms:.2f} ms")
    if result.items:
        print("  Retrieved memories:")
        for item in result.items:
            location = (
                f"{_safe_log_value(item.file_path, 300)}:"
                f"{item.line_start}-{item.line_end}"
            )
            print(
                f"    {item.rank}. {_safe_log_value(item.kind, 40)} "
                f"{_safe_log_value(item.qualified_name, 500)}"
            )
            print(f"       Location: {location}")
            print(f"       Score: {item.score:.3f}")
            print(f"       Why: {', '.join(item.reasons)}")
    if result.warnings:
        print("  Warnings:")
        for warning in result.warnings:
            print(f"    - {_safe_log_value(warning, 500)}")


def _safe_log_value(value: object, limit: int) -> str:
    rendered = "".join(
        character if character.isprintable() else " " for character in str(value)
    )
    return rendered if len(rendered) <= limit else rendered[: limit - 1] + "…"


def _run_local_solve(arguments: argparse.Namespace) -> int:
    """Run one local solve."""

    settings = Settings.from_env()
    request = SolveRequest(
        repo_path=arguments.repo.expanduser().resolve(),
        issue_path=arguments.issue_file.expanduser().resolve(),
        base_ref=arguments.base_ref,
        sandbox_image=arguments.sandbox_image,
    )
    effective_image = request.sandbox_image or settings.sandbox_image
    _validate_prerequisites(request, settings, sandbox_image=effective_image)
    orchestrator = build_orchestrator(settings)
    result = asyncio.run(solve_issue(request, orchestrator, settings))
    _render_result(
        result,
        model=settings.solver_model,
    )
    return (
        0
        if result.outcome is SolveOutcome.COMPLETED and result.diff.strip()
        else 2
    )


def _run_github_gate(arguments: argparse.Namespace) -> int:
    """Run the model-free GitHub command gate and emit safe job outputs."""

    environment = dict(os.environ)
    if arguments.event_file is not None:
        environment["GITHUB_EVENT_PATH"] = str(
            arguments.event_file.expanduser().resolve()
        )
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


def _configure_logging(*, debug: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _flush_langsmith_traces() -> None:
    if os.environ.get("LANGSMITH_TRACING", "false").strip().lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return
    try:
        wait_for_all_tracers()
    except Exception:
        logger.warning("LangSmith trace flush failed; the Sage result is unaffected.")


def _validate_prerequisites(
    request: SolveRequest,
    settings: Settings,
    *,
    sandbox_image: str,
) -> None:
    if not request.repo_path.is_dir():
        raise ConfigurationError(f"Repository path does not exist: {request.repo_path}")
    if not request.issue_path.is_file():
        raise ConfigurationError(f"Issue file does not exist: {request.issue_path}")
    if shutil.which("git") is None:
        raise ConfigurationError("Git executable was not found.")
    if shutil.which("docker") is None:
        raise ConfigurationError("Docker executable was not found.")

    _check_docker_command(
        ["docker", "info", "--format", "{{.ServerVersion}}"],
        failure_message="Docker daemon is not reachable.",
    )
    _check_docker_command(
        ["docker", "image", "inspect", sandbox_image],
        failure_message=f"Docker sandbox image does not exist: {sandbox_image}",
    )
    try:
        settings.runs_dir.expanduser().mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ConfigurationError(
            f"Runs directory cannot be created: {settings.runs_dir}"
        ) from error


def _check_docker_command(command: list[str], *, failure_message: str) -> None:
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        raise ConfigurationError(failure_message) from error
    if result.returncode != 0:
        raise ConfigurationError(failure_message)


def _render_result(result: SolveResult, *, model: str) -> None:
    print("Sage")
    print()
    print(f"Run: {result.run_id}")
    print(f"Base: {result.base_sha[:12]}")
    print(f"Model: {model}")
    print()
    if result.changed_files:
        print("Changed files:")
        for path in result.changed_files:
            print(f"  {path}")
        print()
        print("Summary:")
        print(f"  {result.summary}")
        print()
        print("Workspace:")
        print(f"  {result.workspace_dir}")
        print()
        print("Patch:")
        print(f"  {result.run_dir / 'diff.patch'}")
        return

    print("Agent completed without producing a repository change.")
    print()
    print("Summary:")
    print(f"  {result.summary}")
    if result.remaining_uncertainty:
        print()
        print("Remaining uncertainty:")
        for uncertainty in result.remaining_uncertainty:
            print(f"  {uncertainty}")
    print()
    print("Run artifacts:")
    print(f"  {result.run_dir}")


if __name__ == "__main__":
    raise SystemExit(main())
