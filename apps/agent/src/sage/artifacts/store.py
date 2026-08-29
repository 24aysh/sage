"""Plain-filesystem persistence for local solve runs."""

from __future__ import annotations

import logging
from datetime import datetime

from sage.artifacts.files import write_json_atomic, write_text_atomic
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
            "model": settings.v2_solver_model,
            "runtime": settings.runtime,
            "model_profile": settings.model_profile,
            "research_enabled": settings.research_enabled,
            "web_search_provider": settings.web_search_provider or None,
            "sandbox_image": settings.sandbox_image,
        }
        try:
            write_json_atomic(
                prepared_run.run_dir / "request.json",
                request.model_dump(mode="json"),
            )
            write_json_atomic(prepared_run.run_dir / "metadata.json", metadata)
            write_text_atomic(prepared_run.run_dir / "issue.md", issue_text)
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
            write_json_atomic(
                result.run_dir / "agent-final.json",
                final_output.model_dump(mode="json", exclude_none=True),
            )
            write_json_atomic(
                result.run_dir / "changed-files.json",
                result.changed_files,
            )
            write_text_atomic(result.run_dir / "diff.patch", result.diff)
        except OSError as error:
            raise ArtifactError("Unable to persist final run artifacts.") from error

        logger.info(
            "run result persisted",
            extra={"run_id": result.run_id, "path": str(result.run_dir)},
        )
