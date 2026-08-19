import pytest
from pydantic import ValidationError

from sage.integrations.github.models import GitHubActor, GitHubRepository


def test_repository_rejects_unsafe_owner_path_segment() -> None:
    with pytest.raises(ValidationError, match="valid GitHub.com account"):
        GitHubRepository(
            owner="owner?redirect=https://example.com",
            name="repository",
            repository_id=1,
            html_url="https://github.com/owner/repository",
        )


def test_actor_accepts_github_bot_login_shape() -> None:
    actor = GitHubActor(login="github-actions[bot]", user_id=41898282)

    assert actor.login == "github-actions[bot]"
