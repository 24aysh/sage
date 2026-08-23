"""Typed execution-plan contracts for the sequential V2 runtime."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RetrievalKind(StrEnum):
    """Repository evidence the deterministic context compiler can retrieve."""

    PATH = "path"
    SYMBOL = "symbol"
    LITERAL_SEARCH = "literal_search"
    NEARBY_TESTS = "nearby_tests"
    DIRECT_REFERENCES = "direct_references"


class Complexity(StrEnum):
    """Planner-proposed task complexity used only as bounded evidence."""

    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"


class AcceptanceCriterion(BaseModel):
    """One observable behavior the candidate must satisfy."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    criterion_id: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")
    behavior: str = Field(min_length=1, max_length=1_000)
    verification: str = Field(min_length=1, max_length=500)


class RetrievalRequest(BaseModel):
    """One bounded deterministic repository lookup requested by a model."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: RetrievalKind
    value: str = Field(min_length=1, max_length=300)
    path: str | None = Field(default=None, max_length=500)
    reason: str = Field(min_length=1, max_length=500)


class PlanTask(BaseModel):
    """One sequential implementation task."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")
    objective: str = Field(min_length=1, max_length=1_000)
    relevant_paths: tuple[str, ...] = Field(default=(), max_length=20)
    criterion_ids: tuple[str, ...] = Field(min_length=1, max_length=12)
    depends_on: tuple[str, ...] = Field(default=(), max_length=12)


class VerificationHint(BaseModel):
    """A proposed sandbox check; deterministic policy decides whether to run it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    command: str = Field(min_length=1, max_length=4_000)
    reason: str = Field(min_length=1, max_length=500)
    required: bool = True


class ExecutionPlan(BaseModel):
    """Validated sequential plan produced by the intake role."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    task_summary: str = Field(min_length=1, max_length=2_000)
    acceptance_contract: tuple[AcceptanceCriterion, ...] = Field(
        min_length=1, max_length=12
    )
    safe_assumptions: tuple[str, ...] = Field(default=(), max_length=10)
    tasks: tuple[PlanTask, ...] = Field(min_length=1, max_length=12)
    retrieval_requests: tuple[RetrievalRequest, ...] = Field(default=(), max_length=12)
    verification_hints: tuple[VerificationHint, ...] = Field(default=(), max_length=4)
    risks: tuple[str, ...] = Field(default=(), max_length=10)
    non_blocking_uncertainties: tuple[str, ...] = Field(default=(), max_length=10)
    allowed_write_scopes: tuple[str, ...] = Field(min_length=1, max_length=20)
    complexity: Complexity = Complexity.MEDIUM
    route: Literal["single"] = "single"

    @model_validator(mode="after")
    def validate_references_and_dag(self) -> ExecutionPlan:
        criterion_ids = [item.criterion_id for item in self.acceptance_contract]
        if len(set(criterion_ids)) != len(criterion_ids):
            raise ValueError("Acceptance criterion IDs must be unique.")

        task_ids = [item.task_id for item in self.tasks]
        if len(set(task_ids)) != len(task_ids):
            raise ValueError("Plan task IDs must be unique.")
        known_tasks = set(task_ids)
        known_criteria = set(criterion_ids)
        for task in self.tasks:
            unknown_criteria = set(task.criterion_ids) - known_criteria
            if unknown_criteria:
                raise ValueError("Plan task references an unknown acceptance criterion.")
            unknown_dependencies = set(task.depends_on) - known_tasks
            if unknown_dependencies or task.task_id in task.depends_on:
                raise ValueError("Plan task has an invalid dependency.")

        visiting: set[str] = set()
        visited: set[str] = set()
        dependencies = {item.task_id: set(item.depends_on) for item in self.tasks}

        def visit(task_id: str) -> None:
            if task_id in visiting:
                raise ValueError("Plan task dependencies must be acyclic.")
            if task_id in visited:
                return
            visiting.add(task_id)
            for dependency in dependencies[task_id]:
                visit(dependency)
            visiting.remove(task_id)
            visited.add(task_id)

        for task_id in task_ids:
            visit(task_id)
        return self
