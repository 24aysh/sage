"""Run-bound persistence for reproducible inputs and authoritative evidence."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

from sage.artifacts.files import write_json_atomic, write_text_atomic
from sage.config import Settings
from sage.domain.memory import LegionMemoryRunArtifact
from sage.domain.solve import AgentFinalOutput, PreparedRun, SolveRequest, SolveResult
from sage.domain.usage import RunProvenance
from sage.errors import ArtifactError

logger = logging.getLogger(__name__)
_RUNTIME_LABEL = "v2"
_MODEL_PROFILE_LABEL = "constrained-cross-provider"


class RunArtifacts:
    """Own every atomic artifact written for one solve run."""

    def __init__(self, run_dir: Path) -> None:
        self._run_dir = run_dir

    def initialize(
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
            "model": settings.solver_model,
            "runtime": _RUNTIME_LABEL,
            "model_profile": _MODEL_PROFILE_LABEL,
            "research_enabled": settings.research_enabled,
            "web_search_provider": settings.web_search_provider or None,
            "sandbox_image": settings.sandbox_image,
        }
        try:
            request_payload = request.model_dump(mode="json")
            if request.memory_file is None:
                request_payload.pop("memory_file", None)
            write_json_atomic(prepared_run.run_dir / "request.json", request_payload)
            write_json_atomic(prepared_run.run_dir / "metadata.json", metadata)
            write_text_atomic(prepared_run.run_dir / "issue.md", issue_text)
        except OSError as error:
            raise ArtifactError("Unable to initialize run artifacts.") from error

        logger.info(
            "run artifacts initialized",
            extra={"run_id": prepared_run.run_id, "path": str(prepared_run.run_dir)},
        )

    def write_result(
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

    def write_solver_plan(self, version: int, value: BaseModel) -> Path:
        """Persist one immutable revision and update the latest plan pointer."""

        if version < 1:
            raise ArtifactError("Solver plan version must be positive.")
        path = self._json(Path("solver-plans") / f"{version:02d}.json", value)
        self._json("solver-plan.json", value)
        return path

    def write_research_summary(self, value: BaseModel) -> Path:
        return self._json("research-summary.json", value)

    def write_solver_final(self, value: BaseModel) -> Path:
        return self._json("solver-final.json", value)

    def write_candidate_snapshot(self, value: BaseModel) -> Path:
        return self._json("candidate-snapshot.json", value)

    def write_verification_summary(self, pass_number: int, value: BaseModel) -> Path:
        path = self._json(
            Path("verification") / f"pass-{pass_number}" / "summary.json",
            value,
        )
        self._json("verification-summary.json", value)
        return path

    def write_verification_log(self, pass_number: int, check_id: str, value: str) -> Path:
        name = _safe_stage(check_id)
        return self._text(
            Path("verification") / f"pass-{pass_number}" / f"{name}.log",
            value,
        )

    def write_review(self, value: BaseModel, *, version: int | None = None) -> Path:
        path = self._json("review.json", value)
        if version is not None:
            if version < 1:
                raise ArtifactError("Review version must be positive.")
            self._json(Path("reviews") / f"{version:02d}.json", value)
        return path

    def write_usage(self, value: RunProvenance) -> Path:
        return self._json("usage.json", value)

    def write_legion_memory(self, value: LegionMemoryRunArtifact) -> Path:
        return self._json("legion-memory.json", value)

    def write_terminal(self, value: BaseModel) -> Path:
        return self._json("terminal.json", value)

    def _json(self, relative: str | Path, value: BaseModel) -> Path:
        path = self._run_dir / relative
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            write_json_atomic(path, value.model_dump(mode="json"))
        except OSError as error:
            raise ArtifactError(f"Unable to persist artifact: {path.name}") from error
        return path

    def _text(self, relative: str | Path, value: str) -> Path:
        path = self._run_dir / relative
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            write_text_atomic(path, value)
        except OSError as error:
            raise ArtifactError(f"Unable to persist artifact: {path.name}") from error
        return path


def _safe_stage(value: str) -> str:
    normalized = value.strip().lower().replace("_", "-")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,99}", normalized):
        raise ArtifactError("Artifact stage name is invalid.")
    return normalized
