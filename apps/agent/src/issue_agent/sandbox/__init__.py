"""Repository execution sandbox abstractions."""

from issue_agent.sandbox.base import CommandResult, Sandbox
from issue_agent.sandbox.docker import DockerSandbox

__all__ = ["CommandResult", "DockerSandbox", "Sandbox"]
