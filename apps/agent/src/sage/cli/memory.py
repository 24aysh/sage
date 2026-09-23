"""Standalone memory commands and retrieval artifact publication."""

from __future__ import annotations

import argparse
from pathlib import Path

from sage.artifacts.files import write_json_atomic, write_text_atomic
from sage.cli.output import _render_memory_retrieval
from sage.composition import build_legion_memory_service
from sage.domain.memory import MemoryRetrievalResult, MemoryRetrievalStatus
from sage.errors import LegionMemoryQueryError
from sage.harness.memory.service import LegionMemoryService


def add_parser(subparsers: argparse._SubParsersAction) -> tuple[argparse.ArgumentParser, argparse.ArgumentParser]:
    memory_parser = subparsers.add_parser("memory", help="Build or inspect the local Legion Memory graph.")
    memory_subparsers = memory_parser.add_subparsers(
        dest="memory_command",
        required=True,
    )
    memory_build_parser = memory_subparsers.add_parser("build", help="Build, update, or confirm a repository graph.")
    memory_build_parser.add_argument("--repo", required=True, type=Path)
    memory_build_parser.add_argument("--memory-file", type=Path)
    memory_build_parser.add_argument("--full-rebuild", action="store_true")
    memory_build_parser.add_argument("--debug", action="store_true")
    memory_build_parser.set_defaults(handler=_run_memory_build)

    memory_status_parser = memory_subparsers.add_parser("status", help="Inspect graph readiness and provenance.")
    memory_status_parser.add_argument("--repo", required=True, type=Path)
    memory_status_parser.add_argument("--memory-file", type=Path)
    memory_status_parser.add_argument("--debug", action="store_true")
    memory_status_parser.set_defaults(handler=_run_memory_status)

    memory_retrieve_parser = memory_subparsers.add_parser("retrieve", help="Retrieve Issue-relevant context from a ready graph.")
    memory_retrieve_parser.add_argument("--repo", required=True, type=Path)
    memory_retrieve_parser.add_argument("--issue-file", required=True, type=Path)
    memory_retrieve_parser.add_argument("--memory-file", required=True, type=Path)
    memory_retrieve_parser.add_argument("--debug", action="store_true")
    memory_retrieve_parser.set_defaults(handler=_run_memory_retrieve)
    return memory_build_parser, memory_retrieve_parser


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
    context_file = (
        _write_memory_retrieval_context(result, issue_file=issue_file)
        if result.status is not MemoryRetrievalStatus.UNAVAILABLE
        else None
    )
    _render_memory_retrieval(result, context_file=context_file)
    return 1 if result.status is MemoryRetrievalStatus.UNAVAILABLE else 0


def _write_memory_retrieval_context(
    result: MemoryRetrievalResult,
    *,
    issue_file: Path,
) -> Path:
    """Atomically save the latest bounded retrieval beside its SQLite graph."""

    context_file = result.memory_file.with_suffix(".context.md")
    context = result.context or "_No Issue-relevant context was retrieved._"
    document = (
        "# Legion Memory retrieved context\n\n"
        "> Graph-derived navigation context. Verify locations and behavior "
        "against source.\n\n"
        f"- Issue file: `{issue_file}`\n"
        f"- Memory file: `{result.memory_file}`\n"
        f"- Indexed SHA: `{result.indexed_sha or 'unavailable'}`\n"
        f"- Status: `{result.status.value}`\n"
        f"- Outcome: `{result.outcome.value}`\n"
        f"- Context characters: {result.context_chars}\n"
        f"- Truncated: {'yes' if result.truncated else 'no'}\n\n"
        "## Context passed to Sage\n\n"
        f"{context}\n"
    )
    try:
        write_text_atomic(context_file, document)
        write_json_atomic(result.memory_file.with_suffix(".retrieval.json"), result.model_dump(mode="json"))
    except OSError as error:
        raise LegionMemoryQueryError(
            "Unable to save retrieved context: "
            f"{type(error).__name__}: {str(error)[:300]}"
        ) from error
    return context_file
