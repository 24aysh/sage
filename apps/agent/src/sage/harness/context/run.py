"""Explicit run-scoped dependencies for solve coordination."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, Any
from pathlib import Path

from sage.domain.solve import AgentFinalOutput, PreparedRun
from sage.domain.navigation import NavigationHook
from sage.harness.context.instructions import RoleInstructions

if TYPE_CHECKING:
    from sage.artifacts.store import RunArtifacts
    from sage.config import Settings
    from sage.harness.memory.session import MemorySession
    from sage.repository.service import Repository


@dataclass(frozen=True)
class SolveContext:
    """Trusted controller state available to agents and capabilities."""

    prepared_run: PreparedRun
    repository: Repository
    settings: Settings
    artifacts: RunArtifacts
    memory: MemorySession | None = None
    navigation: NavigationHook | None = None
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
    memory: SolverMemorySession | None
    instructions: RoleInstructions


class SolverMemorySession(Protocol):
    """Narrow run-scoped memory surface consumed by Solver tool binding."""

    service: Any
    repo_root: Path
    memory_file: Path
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
