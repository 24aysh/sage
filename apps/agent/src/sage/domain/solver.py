"""Provider-neutral contracts for the V2 tool-driven Solver."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SolverPlanTask(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_.-]+$")
    objective: str = Field(min_length=1, max_length=1_000)
    expected_paths: tuple[str, ...] = Field(default=(), max_length=20)
    criterion_ids: tuple[str, ...] = Field(default=(), max_length=20)


class SolverAcceptanceCriterion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    criterion_id: str = Field(
        min_length=1,
        max_length=40,
        pattern=r"^[A-Za-z0-9_.-]+$",
    )
    requirement: str = Field(min_length=1, max_length=1_000)


class SolverPlan(BaseModel):
    """Complete Solver-authored plan persisted before repository mutation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1"] = "1"
    issue_summary: str = Field(min_length=1, max_length=2_000)
    approach: str = Field(min_length=1, max_length=4_000)
    tasks: tuple[SolverPlanTask, ...] = Field(min_length=1, max_length=30)
    acceptance_criteria: tuple[SolverAcceptanceCriterion, ...] = Field(
        min_length=1,
        max_length=30,
    )
    relevant_paths: tuple[str, ...] = Field(default=(), max_length=50)
    verification_commands: tuple[str, ...] = Field(default=(), max_length=10)
    assumptions: tuple[str, ...] = Field(default=(), max_length=20)
    risks: tuple[str, ...] = Field(default=(), max_length=20)
    status: Literal["implementable", "blocked"]
    blocker: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def validate_contract(self) -> SolverPlan:
        task_ids = [task.task_id for task in self.tasks]
        criterion_ids = [item.criterion_id for item in self.acceptance_criteria]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("Solver plan task IDs must be unique.")
        if len(criterion_ids) != len(set(criterion_ids)):
            raise ValueError("Solver plan criterion IDs must be unique.")
        known = set(criterion_ids)
        for task in self.tasks:
            unknown = set(task.criterion_ids) - known
            if unknown:
                raise ValueError(
                    f"Task {task.task_id} references unknown criteria: "
                    + ", ".join(sorted(unknown))
                )
        if self.status == "blocked" and not self.blocker:
            raise ValueError("A blocked Solver plan requires a blocker.")
        if self.status == "implementable" and self.blocker is not None:
            raise ValueError("An implementable Solver plan cannot contain a blocker.")
        return self

    def digest(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class SavedSolverPlan(BaseModel):
    """Controller metadata stored with one accepted plan revision."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int = Field(ge=1)
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    revision_reason: str | None = Field(default=None, min_length=1, max_length=1_000)
    plan: SolverPlan


class SolverOutcome(StrEnum):
    IMPLEMENTED = "implemented"
    NO_CHANGE = "no_change"
    BLOCKED = "blocked"
    UNRESOLVED = "unresolved"


class SolverFinalResult(BaseModel):
    """Structured Solver terminal result; Git owns candidate details."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: SolverOutcome
    summary: str = Field(min_length=1, max_length=2_000)
    plan_version: int = Field(ge=1)
    verification_claims: tuple[str, ...] = Field(default=(), max_length=20)
    remaining_uncertainty: tuple[str, ...] = Field(default=(), max_length=20)


class CandidateSnapshot(BaseModel):
    """Controller-derived candidate reviewed by the independent Reviewer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    base_sha: str = Field(min_length=7, max_length=64)
    changed_files: tuple[str, ...] = Field(min_length=1)
    diff: str = Field(min_length=1)
    diff_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan_version: int = Field(ge=1)
    plan_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    solver_summary: str = Field(min_length=1, max_length=2_000)
    verification_claims: tuple[str, ...] = ()
    remaining_uncertainty: tuple[str, ...] = ()
