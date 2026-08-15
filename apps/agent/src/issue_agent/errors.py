"""Application-level exceptions surfaced by IssueAgent."""


class IssueAgentError(Exception):
    """Base class for expected IssueAgent failures."""


class ConfigurationError(IssueAgentError):
    """Raised when required application configuration is invalid."""


class RepositoryError(IssueAgentError):
    """Raised when a repository operation fails."""


class WorkspaceError(IssueAgentError):
    """Raised when an isolated run workspace cannot be prepared."""


class PathSafetyError(RepositoryError):
    """Raised when a requested repository path is unsafe."""


class SandboxError(IssueAgentError):
    """Raised when the Docker sandbox lifecycle fails."""


class CommandExecutionError(RepositoryError):
    """Raised when a required repository command fails."""


class CommandTimeoutError(CommandExecutionError):
    """Raised when a required repository command times out."""


class PatchError(RepositoryError):
    """Raised when a repository patch is unsafe or cannot be applied."""


class AgentRuntimeError(IssueAgentError):
    """Raised when the configured agent runtime fails."""


class ArtifactError(IssueAgentError):
    """Raised when run artifacts cannot be persisted."""
