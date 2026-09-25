"""Run the retrieval shortlist/Jev noise evaluation."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from pydantic import ValidationError
from tqdm import tqdm
from tqdm.contrib.logging import logging_redirect_tqdm

from evals.retrieval.models import EvaluationResults
from evals.retrieval.report import terminal_summary
from evals.retrieval.runner import (
    EvaluationPreflightError,
    prepare_evaluation,
    run_evaluation,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate production lexical/graph retrieval before and after the Jev "
            "file relevance filter. This makes paid TypeSafe API calls."
        )
    )
    parser.add_argument("--repo", required=True, type=Path, help="Exact Git repository root")
    parser.add_argument("--issues-dir", required=True, type=Path)
    parser.add_argument("--issue-count", required=True, type=int)
    parser.add_argument("--graph", required=True, type=Path, help="Ready retrieval SQLite index")
    parser.add_argument("--output-dir", type=Path, help="Absent or empty destination directory")
    return parser


def _saved_results(output_dir: Path) -> EvaluationResults | None:
    try:
        return EvaluationResults.model_validate_json(
            (output_dir / "results.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError, ValidationError):
        return None


def main(arguments: list[str] | None = None) -> int:
    args = _parser().parse_args(arguments)
    try:
        prepared = prepare_evaluation(
            repo=args.repo,
            issues_dir=args.issues_dir,
            issue_count=args.issue_count,
            graph=args.graph,
            output_dir=args.output_dir,
        )
    except EvaluationPreflightError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    except OSError as error:
        print(f"ERROR: unable to prepare evaluation output: {error}", file=sys.stderr)
        return 1

    print(f"Evaluation output: {prepared.output_dir}")
    print(
        "Jev evaluation mode: on "
        f"(model={prepared.settings.model}, score>={prepared.settings.score_threshold}, "
        f"confidence>={prepared.settings.confidence_threshold})"
    )
    progress = tqdm(
        total=len(prepared.dataset.issues),
        unit="issue",
        desc="Retrieval evaluation",
        file=sys.stderr,
        disable=False,
    )
    try:
        with logging_redirect_tqdm():
            results, exit_code = asyncio.run(run_evaluation(prepared, progress=progress))
    except (KeyboardInterrupt, asyncio.CancelledError):
        saved = _saved_results(prepared.output_dir)
        if saved is not None:
            print(terminal_summary(saved))
        print("Retrieval evaluation interrupted.", file=sys.stderr)
        return 130
    except (OSError, ValueError) as error:
        saved = _saved_results(prepared.output_dir)
        if saved is not None:
            print(terminal_summary(saved))
        print(f"ERROR: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    except Exception as error:
        logging.getLogger(__name__).exception("Retrieval evaluation failed")
        saved = _saved_results(prepared.output_dir)
        if saved is not None:
            print(terminal_summary(saved))
        print(f"ERROR: {type(error).__name__}: {str(error)[:300]}", file=sys.stderr)
        return 1
    finally:
        progress.close()
    print(terminal_summary(results))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
