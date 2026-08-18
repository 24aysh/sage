"""Repository execution sandbox abstractions."""

from sage.sandbox.base import CommandResult, Sandbox
from sage.sandbox.docker import DockerSandbox

__all__ = ["CommandResult", "DockerSandbox", "Sandbox"]
