"""CLI dispatch, error policy, logging, and trace shutdown."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Callable

from langchain_core.tracers.langchain import wait_for_all_tracers

from sage.cli import github, memory, solve
from sage.errors import SageError

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
    solve.add_parser(subparsers)
    memory.add_parser(subparsers)
    github.add_parser(subparsers)
    return parser


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
