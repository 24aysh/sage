"""Project-owned agent runtime contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from issue_agent.domain.requests import PreparedRun
from issue_agent.domain.results import AgentFinalOutput

if TYPE_CHECKING:
    from issue_agent.config import Settings
    from issue_agent.repository import RepositoryTools
    from issue_agent.sandbox.base import Sandbox


@dataclass(frozen=True)
class RuntimeContext:
    """Trusted controller state made available to runtime tool adapters."""

    prepared_run: PreparedRun
    sandbox: Sandbox
    repository: RepositoryTools
    settings: Settings


class AgentRuntime(Protocol):
    """Minimal interface implemented by the temporary V0 runtime."""

    async def solve(
        self,
        *,
        issue_text: str,
        context: RuntimeContext,
    ) -> AgentFinalOutput:
        """Inspect and modify the prepared repository for one issue."""
        ...
