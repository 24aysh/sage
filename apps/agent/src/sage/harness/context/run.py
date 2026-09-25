"""Explicit run-scoped dependencies for solve coordination."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, Any
from pathlib import Path

from sage.domain.solve import AgentFinalOutput, PreparedRun
from sage.harness.context.instructions import RoleInstructions

if TYPE_CHECKING:
    from sage.artifacts.store import RunArtifacts
    from sage.config import Settings
    from sage.harness.retrieval.session import RetrievalSession
    from sage.repository.service import Repository


@dataclass(frozen=True)
class SolveContext:
    """Trusted controller state available to agents and capabilities."""

    prepared_run: PreparedRun
    repository: Repository
    settings: Settings
    artifacts: RunArtifacts
    retrieval: RetrievalSession | None = None
    instructions: RoleInstructions = field(default_factory=RoleInstructions)


class SolveEngine(Protocol):
    """Narrow workflow boundary implemented by the solve orchestrator."""

    async def solve(
        self,
        *,
        issue_text: str,
        context: SolveContext,
    ) -> AgentFinalOutput:
        """Inspect and modify one prepared repository for an Issue."""
        ...


class RepositoryContext(Protocol):
    """Current repository capabilities available to read tool bindings."""

    repository: Repository


class SolverContext(RepositoryContext, Protocol):
    """Run-scoped capabilities needed by the Solver role."""

    prepared_run: PreparedRun
    settings: Settings
    retrieval: SolverRetrievalSession | None
    instructions: RoleInstructions


class SolverRetrievalSession(Protocol):
    """Narrow run-scoped retrieval surface consumed by Solver tool binding."""

    service: Any
    repo_root: Path
    index_file: Path
    tools_enabled: bool

    def enrich(self, **arguments: Any) -> str: ...
    def begin_session(self, *, initial_visible: bool) -> str: ...
    def invalidate(self, *paths: str) -> None: ...
    def record_schemas(self, characters: int) -> None: ...
    def filter_response(self, result: dict[str, object]) -> dict[str, object]: ...

    def record_tool_call(
        self,
        tool_name: str,
        result: dict[str, object],
        duration_ms: float,
    ) -> None: ...
