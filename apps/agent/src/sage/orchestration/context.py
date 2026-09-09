"""Explicit run-scoped dependencies for solve coordination."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from sage.domain.solve import AgentFinalOutput, PreparedRun

if TYPE_CHECKING:
    from sage.artifacts.store import RunArtifacts
    from sage.config import Settings
    from sage.legion_memory.session import MemorySession
    from sage.repository.service import Repository


@dataclass(frozen=True)
class SolveContext:
    """Trusted controller state available to agents and capabilities."""

    prepared_run: PreparedRun
    repository: Repository
    settings: Settings
    artifacts: RunArtifacts
    memory: MemorySession | None = None


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
