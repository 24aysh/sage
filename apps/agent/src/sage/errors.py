"""Application-level exceptions surfaced by Sage."""


class SageError(Exception):
    """Base class for expected Sage failures."""


class ConfigurationError(SageError):
    """Raised when required application configuration is invalid."""


class RepositoryError(SageError):
    """Raised when a repository operation fails."""


class WorkspaceError(SageError):
    """Raised when an isolated run workspace cannot be prepared."""


class HostGitError(SageError):
    """Raised when the trusted controller cannot execute Git."""


class HostGitTimeoutError(HostGitError):
    """Raised when a trusted host-side Git command exceeds its timeout."""


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


class ModelAuthenticationError(AgentRuntimeError):
    """Raised when the model provider rejects configured authentication."""


class ModelAPIError(AgentRuntimeError):
    """Raised when an authenticated model API request is rejected."""


class ModelQuotaError(AgentRuntimeError):
    """Raised when model credits or configured account limits are exhausted."""


class ModelRateLimitError(AgentRuntimeError):
    """Raised when a temporary model rate limit outlives bounded retries."""


class ArtifactError(SageError):
    """Raised when run artifacts cannot be persisted."""


class GitHubIntegrationError(SageError):
    """Base class for expected GitHub integration failures."""


class GitHubConfigurationError(ConfigurationError, GitHubIntegrationError):
    """Raised when trusted GitHub controller configuration is invalid."""


class GitHubEventError(GitHubIntegrationError):
    """Raised when a GitHub event cannot be validated safely."""


class GitHubApiError(GitHubIntegrationError):
    """Raised when a GitHub API operation fails safely."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        request_id: str | None = None,
        ambiguous: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.request_id = request_id
        self.ambiguous = ambiguous


class GitHubGateError(GitHubIntegrationError):
    """Raised when a supported invocation cannot be gated safely."""


class GitHubContextError(GitHubIntegrationError):
    """Raised when GitHub Issue context cannot be assembled safely."""


class GitHubStatusError(GitHubIntegrationError):
    """Raised when an invocation status cannot be transitioned safely."""


class GitHubPublicationError(GitHubIntegrationError):
    """Raised when a solved candidate cannot be published safely."""


class GitHubOrphanBranchError(GitHubPublicationError):
    """Raised when a branch was pushed but its Pull Request was not created."""

    def __init__(self, message: str, *, branch_url: str) -> None:
        super().__init__(message)
        self.branch_url = branch_url
