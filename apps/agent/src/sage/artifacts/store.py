"""Plain-filesystem persistence for local solve runs."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from sage.config import Settings
from sage.domain.requests import PreparedRun, SolveRequest
from sage.domain.results import AgentFinalOutput, SolveResult
from sage.errors import ArtifactError

logger = logging.getLogger(__name__)


class ArtifactStore:
    """Persist the reproducible inputs and authoritative outputs of one run."""

    def initialize_run(
        self,
        *,
        request: SolveRequest,
        prepared_run: PreparedRun,
        issue_text: str,
        settings: Settings,
    ) -> None:
        metadata = {
            "run_id": prepared_run.run_id,
            "created_at": datetime.now().astimezone().isoformat(),
            "base_ref": prepared_run.base_ref,
            "base_sha": prepared_run.base_sha,
            "model": settings.openai_model,
            "sandbox_image": settings.sandbox_image,
        }
        try:
            _write_json(
                prepared_run.run_dir / "request.json",
                request.model_dump(mode="json"),
            )
            _write_json(prepared_run.run_dir / "metadata.json", metadata)
            _write_text(prepared_run.run_dir / "issue.md", issue_text)
        except OSError as error:
            raise ArtifactError("Unable to initialize run artifacts.") from error

        logger.info(
            "run artifacts initialized",
            extra={"run_id": prepared_run.run_id, "path": str(prepared_run.run_dir)},
        )

    def persist_result(
        self,
        *,
        final_output: AgentFinalOutput,
        result: SolveResult,
    ) -> None:
        try:
            _write_json(
                result.run_dir / "agent-final.json",
                final_output.model_dump(mode="json"),
            )
            _write_json(result.run_dir / "changed-files.json", result.changed_files)
            _write_text(result.run_dir / "diff.patch", result.diff)
        except OSError as error:
            raise ArtifactError("Unable to persist final run artifacts.") from error

        logger.info(
            "run result persisted",
            extra={"run_id": result.run_id, "path": str(result.run_dir)},
        )


def _write_json(path: Path, value: object) -> None:
    _write_text(path, f"{json.dumps(value, indent=2, sort_keys=True)}\n")


def _write_text(path: Path, value: str) -> None:
    temporary_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary_path.write_text(value, encoding="utf-8")
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)
