"""Application-level exceptions surfaced by Sage."""


class SageError(Exception):
    """Base class for expected Sage failures."""


class ConfigurationError(SageError):
    """Raised when required application configuration is invalid."""


class RepositoryError(SageError):
    """Raised when a repository operation fails."""


class WorkspaceError(SageError):
    """Raised when an isolated run workspace cannot be prepared."""


class PathSafetyError(RepositoryError):
    """Raised when a requested repository path is unsafe."""


class SandboxError(SageError):
    """Raised when the Docker sandbox lifecycle fails."""


class CommandExecutionError(RepositoryError):
    """Raised when a required repository command fails."""


class CommandTimeoutError(CommandExecutionError):
    """Raised when a required repository command times out."""


class PatchError(RepositoryError):
    """Raised when a repository patch is unsafe or cannot be applied."""


class AgentRuntimeError(SageError):
    """Raised when the configured agent runtime fails."""


class ArtifactError(SageError):
    """Raised when run artifacts cannot be persisted."""
