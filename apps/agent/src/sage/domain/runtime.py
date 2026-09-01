"""Project-owned agent runtime contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from sage.domain.requests import PreparedRun
from sage.domain.results import AgentFinalOutput

if TYPE_CHECKING:
    from sage.config import Settings
    from sage.memory.api import MemorySession
    from sage.memory.models import RepositoryIdentity
    from sage.repository import RepositoryTools
    from sage.sandbox.base import Sandbox


@dataclass(frozen=True)
class RuntimeContext:
    """Trusted controller state made available to runtime tool adapters."""

    prepared_run: PreparedRun
    sandbox: Sandbox
    repository: RepositoryTools
    settings: Settings
    memory_identity: RepositoryIdentity | None = None
    memory_session: MemorySession | None = None


class AgentRuntime(Protocol):
    """Minimal interface implemented by the active coding-agent runtime."""

    async def solve(
        self,
        *,
        issue_text: str,
        context: RuntimeContext,
    ) -> AgentFinalOutput:
        """Inspect and modify the prepared repository for one issue."""
        ...
