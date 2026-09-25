"""Standalone repository-index commands and retrieval artifact publication."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from sage.artifacts.files import write_json_atomic, write_text_atomic
from sage.cli.output import _render_retrieval
from sage.composition import build_retrieval_service, build_relevance_filter
from sage.config import JevSettings
from sage.domain.retrieval import RetrievalBudgets, RetrievalResult, RetrievalStatus
from sage.errors import RetrievalQueryError
from sage.harness.retrieval.service import RepositoryRetrievalService


def add_parser(subparsers: argparse._SubParsersAction) -> tuple[argparse.ArgumentParser, argparse.ArgumentParser]:
    retrieval_parser = subparsers.add_parser(
        "retrieval",
        aliases=["memory"],
        help="Build or inspect the local repository retrieval index.",
    )
    retrieval_subparsers = retrieval_parser.add_subparsers(
        dest="retrieval_command",
        required=True,
    )
    build_parser = retrieval_subparsers.add_parser("build", help="Build, update, or confirm a repository index.")
    build_parser.add_argument("--repo", required=True, type=Path)
    build_parser.add_argument("--index-file", "--memory-file", dest="index_file", type=Path)
    build_parser.add_argument("--full-rebuild", action="store_true")
    build_parser.add_argument("--debug", action="store_true")
    build_parser.set_defaults(handler=_run_retrieval_build)

    status_parser = retrieval_subparsers.add_parser("status", help="Inspect index readiness and provenance.")
    status_parser.add_argument("--repo", required=True, type=Path)
    status_parser.add_argument("--index-file", "--memory-file", dest="index_file", type=Path)
    status_parser.add_argument("--debug", action="store_true")
    status_parser.set_defaults(handler=_run_retrieval_status)

    retrieve_parser = retrieval_subparsers.add_parser("retrieve", help="Retrieve Issue-relevant context from a ready index.")
    retrieve_parser.add_argument("--repo", required=True, type=Path)
    retrieve_parser.add_argument("--issue-file", required=True, type=Path)
    retrieve_parser.add_argument("--index-file", "--memory-file", dest="index_file", required=True, type=Path)
    retrieve_parser.add_argument("--debug", action="store_true")
    retrieve_parser.set_defaults(handler=_run_retrieval_retrieve)
    return build_parser, retrieve_parser


def _run_retrieval_build(arguments: argparse.Namespace) -> int:
    """Run the strict standalone graph build command."""

    result = build_retrieval_service().build_or_update_graph_tool(
        repo_root=arguments.repo,
        index_file=arguments.index_file,
        full_rebuild=arguments.full_rebuild,
    )
    print("Repository retrieval index: ready")
    print(f"  Index file: {result.index_file}")
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


def _run_retrieval_status(arguments: argparse.Namespace) -> int:
    """Print a bounded graph health and provenance summary."""

    stats = build_retrieval_service().graph_stats(
        repo_root=arguments.repo,
        index_file=arguments.index_file,
    )
    print(f"Repository retrieval index: {stats.status.value}")
    print(f"  Index file: {stats.index_file}")
    if stats.status.value != "ready":
        print("  Build the index with: sage retrieval build --repo <repository>")
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


def _run_retrieval_retrieve(arguments: argparse.Namespace) -> int:
    """Print lexical retrieval after the same optional relevance gate as solve."""

    issue_file = arguments.issue_file.expanduser().resolve()
    if not issue_file.is_file():
        raise RetrievalQueryError(f"Issue file does not exist: {issue_file}")
    try:
        issue_text = issue_file.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise RetrievalQueryError(
            f"Unable to read Issue file: {type(error).__name__}: {str(error)[:300]}"
        ) from error
    settings = JevSettings.from_env()
    result = build_retrieval_service().retrieve_issue_context(
        issue_text=issue_text,
        repo_root=arguments.repo,
        index_file=arguments.index_file,
        budgets=RetrievalBudgets(max_chars=50_000 if settings.mode != "off" else 12_000),
    )
    async def filter_context() -> RetrievalResult:
        relevance = build_relevance_filter(settings)
        try:
            return await relevance.apply(issue=issue_text, retrieval=result, max_chars=12_000,
                report_writer=lambda report: write_json_atomic(
                    result.index_file.with_suffix(".relevance.json"), report))
        finally:
            await relevance.aclose()

    result = asyncio.run(filter_context())
    context_file = (
        _write_retrieval_context(result, issue_file=issue_file)
        if result.status is not RetrievalStatus.UNAVAILABLE
        else None
    )
    _render_retrieval(result, context_file=context_file)
    return 1 if (result.status is RetrievalStatus.UNAVAILABLE or
                 result.relevance_filter and result.relevance_filter.status == "unavailable") else 0


def _write_retrieval_context(
    result: RetrievalResult,
    *,
    issue_file: Path,
) -> Path:
    """Atomically save the latest bounded retrieval beside its SQLite graph."""

    context_file = result.index_file.with_suffix(".context.md")
    context = result.context or "_No Issue-relevant context was retrieved._"
    document = (
        "# Repository retrieval context\n\n"
        "> Graph-derived navigation context. Verify locations and behavior "
        "against source.\n\n"
        f"- Issue file: `{issue_file}`\n"
        f"- Index file: `{result.index_file}`\n"
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
        write_json_atomic(result.index_file.with_suffix(".retrieval.json"), result.model_dump(mode="json"))
    except OSError as error:
        raise RetrievalQueryError(
            "Unable to save retrieved context: "
            f"{type(error).__name__}: {str(error)[:300]}"
        ) from error
    return context_file
