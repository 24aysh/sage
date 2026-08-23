"""Fixed, atomic V2 stage artifact persistence."""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel

from sage.artifacts.files import write_json_atomic, write_text_atomic
from sage.domain.usage import RunProvenance
from sage.errors import ArtifactError

_SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,99}$")


class V2ArtifactStore:
    """Persist V2 artifacts through fixed categories and safe controller names."""

    def __init__(self, run_dir: Path) -> None:
        self._run_dir = run_dir

    def write_repository_map(self, value: BaseModel) -> Path:
        return self._json("repository-map.json", value)

    def write_intake(self, value: BaseModel) -> Path:
        return self._json("intake.json", value)

    def write_plan(self, value: BaseModel) -> Path:
        return self._json("plan.json", value)

    def write_autonomy_contract(self, value: BaseModel) -> Path:
        return self._json("autonomy-contract.json", value)

    def write_context(self, stage: str, call_number: int, content: str) -> Path:
        name = _safe_stage(stage)
        return self._text(Path("contexts") / f"{call_number:02d}-{name}.txt", content)

    def write_proposal(self, stage: str, call_number: int, patch: str) -> Path:
        name = _safe_stage(stage)
        return self._text(Path("proposals") / f"{call_number:02d}-{name}.patch", patch)

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

    def write_review(self, value: BaseModel) -> Path:
        return self._json("review.json", value)

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
