"""Trusted runtime selection shared by local and GitHub workflows."""

from sage.config import Settings
from sage.domain.runtime import AgentRuntime
from sage.runtimes.langgraph import LangGraphRuntime
from sage.runtimes.v2 import V2GraphRuntime


def build_runtime(settings: Settings) -> AgentRuntime:
    """Build the explicitly configured V1 or V2 runtime."""

    if settings.runtime == "v2-prototype":
        return V2GraphRuntime(settings)
    return LangGraphRuntime(settings)
