"""Fixed, atomic V2 stage artifact persistence."""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel

from sage.artifacts.files import write_json_atomic, write_text_atomic
from sage.domain.admission import AdmissionContextSnapshot, AdmissionContextSummary
from sage.domain.usage import RunProvenance
from sage.errors import ArtifactError

_SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,99}$")


class V2ArtifactStore:
    """Persist V2 artifacts through fixed categories and safe controller names."""

    def __init__(self, run_dir: Path) -> None:
        self._run_dir = run_dir

    def write_solver_plan(self, version: int, value: BaseModel) -> Path:
        """Persist one immutable revision and update the latest plan pointer."""

        if version < 1:
            raise ArtifactError("Solver plan version must be positive.")
        path = self._json(Path("solver-plans") / f"{version:02d}.json", value)
        self._json("solver-plan.json", value)
        return path

    def write_admission_context(self, value: AdmissionContextSnapshot) -> Path:
        return self._json("admission-context.json", value)

    def write_admission_context_summary(
        self,
        value: AdmissionContextSnapshot,
    ) -> Path:
        summary = AdmissionContextSummary(
            base_sha=value.base_sha,
            issue_digest=value.issue_digest,
            context_digest=value.digest,
            requirement_count=len(value.requirements),
            evidence_count=len(value.evidence),
            external_source_count=sum(
                item.source_type.value != "repository" for item in value.evidence
            ),
            relevant_paths=value.relevant_paths,
        )
        return self._json("admission-context-summary.json", summary)

    def write_admission_final(self, value: BaseModel) -> Path:
        return self._json("admission-final.json", value)

    def write_clarification(self, value: BaseModel) -> Path:
        return self._json("clarification.json", value)

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

    def write_terminal(self, value: BaseModel) -> Path:
        return self._json("terminal.json", value)

    def _json(self, relative: str | Path, value: BaseModel) -> Path:
        path = self._run_dir / relative
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            write_json_atomic(path, value.model_dump(mode="json"))
        except OSError as error:
            raise ArtifactError(f"Unable to persist V2 artifact: {path.name}") from error
        return path

    def _text(self, relative: str | Path, value: str) -> Path:
        path = self._run_dir / relative
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            write_text_atomic(path, value)
        except OSError as error:
            raise ArtifactError(f"Unable to persist V2 artifact: {path.name}") from error
        return path


def _safe_stage(value: str) -> str:
    normalized = value.strip().lower().replace("_", "-")
    if not _SAFE_NAME.fullmatch(normalized):
        raise ArtifactError("V2 artifact stage name is invalid.")
    return normalized
