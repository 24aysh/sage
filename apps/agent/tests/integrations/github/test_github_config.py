import pytest

from sage.errors import GitHubConfigurationError
from sage.integrations.github.config import GitHubSettings


def test_github_settings_loads_defaults_without_model_key() -> None:
    settings = GitHubSettings.from_env({"SAGE_GITHUB_TOKEN": "github-secret"})

    assert settings.github_token == "github-secret"
    assert settings.api_url == "https://api.github.com"
    assert settings.api_timeout_seconds == 30
    assert settings.max_comments == 20
    assert settings.max_comment_pages == 5
    assert settings.max_context_chars == 40_000
    assert "github-secret" not in repr(settings)


def test_github_settings_loads_bounded_overrides() -> None:
    settings = GitHubSettings.from_env(
        {
            "SAGE_GITHUB_TOKEN": "github-secret",
            "SAGE_GITHUB_API_TIMEOUT_SECONDS": "45",
            "SAGE_GITHUB_MAX_COMMENTS": "10",
            "SAGE_GITHUB_MAX_COMMENT_PAGES": "3",
            "SAGE_GITHUB_MAX_CONTEXT_CHARS": "50000",
        }
    )

    assert settings.api_timeout_seconds == 45
    assert settings.max_comments == 10
    assert settings.max_comment_pages == 3
    assert settings.max_context_chars == 50_000


def test_github_settings_requires_token() -> None:
    with pytest.raises(GitHubConfigurationError, match="SAGE_GITHUB_TOKEN"):
        GitHubSettings.from_env({})


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("SAGE_GITHUB_API_TIMEOUT_SECONDS", "0"),
        ("SAGE_GITHUB_MAX_COMMENTS", "101"),
        ("SAGE_GITHUB_MAX_COMMENT_PAGES", "0"),
        ("SAGE_GITHUB_MAX_CONTEXT_CHARS", "1999"),
    ],
)
def test_github_settings_rejects_invalid_bounds(name: str, value: str) -> None:
    with pytest.raises(GitHubConfigurationError, match="Invalid GitHub configuration"):
        GitHubSettings.from_env(
            {
                "SAGE_GITHUB_TOKEN": "github-secret",
                name: value,
            }
        )
