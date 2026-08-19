import json
from pathlib import Path

import pytest

from sage.errors import GitHubEventError
from sage.integrations.github.commands import SageCommand
from sage.integrations.github.events import (
    MAX_EVENT_BYTES,
    load_issue_comment_event,
    parse_issue_comment_event,
)

FIXTURES = Path(__file__).parents[2] / "fixtures" / "github"
BASE_SHA = "a" * 40


def test_load_issue_comment_event_normalizes_supported_solve() -> None:
    event_path = FIXTURES / "issue_solve.json"

    invocation = load_issue_comment_event(_environment(event_path))

    assert invocation.command is SageCommand.SOLVE
    assert invocation.repository.full_name == "24aysh/example"
    assert invocation.repository.repository_id == 123
    assert invocation.issue.number == 17
    assert invocation.issue.is_pull_request is False
    assert invocation.actor.login == "maintainer"
    assert invocation.comment.comment_id == 1001
    assert invocation.default_branch == "main"
    assert invocation.base_sha == BASE_SHA
    assert invocation.actions_run.run_id == 9001
    assert invocation.actions_run.attempt == 1
    assert invocation.actions_run.html_url.endswith("/actions/runs/9001")


def test_load_issue_comment_event_maps_fix_alias_to_solve() -> None:
    event_path = FIXTURES / "issue_fix.json"

    invocation = load_issue_comment_event(_environment(event_path))

    assert invocation.command is SageCommand.SOLVE
    assert invocation.comment.body == "/sage fix"


def test_load_issue_comment_event_preserves_ordinary_comment_as_ignored() -> None:
    event_path = FIXTURES / "issue_ordinary_comment.json"

    invocation = load_issue_comment_event(_environment(event_path))

    assert invocation.command is None
    assert invocation.issue.is_pull_request is False


def test_load_issue_comment_event_marks_pull_request_conversation() -> None:
    event_path = FIXTURES / "pull_request_solve.json"

    invocation = load_issue_comment_event(_environment(event_path))

    assert invocation.command is SageCommand.SOLVE
    assert invocation.issue.is_pull_request is True
    assert invocation.issue.html_url.endswith("/pull/17")


@pytest.mark.parametrize(
    ("fixture", "message"),
    [
        ("missing_actor.json", "missing required fields"),
        ("repository_mismatch.json", "repository identity is inconsistent"),
    ],
)
def test_load_issue_comment_event_rejects_malformed_payloads(
    fixture: str,
    message: str,
) -> None:
    event_path = FIXTURES / fixture

    with pytest.raises(GitHubEventError, match=message):
        load_issue_comment_event(_environment(event_path))


def test_parse_issue_comment_event_rejects_non_created_action() -> None:
    payload = _payload("issue_solve.json")
    payload["action"] = "edited"

    with pytest.raises(GitHubEventError, match="newly created"):
        parse_issue_comment_event(payload, _environment())


def test_parse_issue_comment_event_rejects_environment_repository_mismatch() -> None:
    environment = _environment()
    environment["GITHUB_REPOSITORY"] = "24aysh/other"

    with pytest.raises(GitHubEventError, match="Actions environment"):
        parse_issue_comment_event(_payload("issue_solve.json"), environment)


def test_parse_issue_comment_event_rejects_sender_mismatch() -> None:
    payload = _payload("issue_solve.json")
    sender = payload["sender"]
    assert isinstance(sender, dict)
    sender["id"] = 999

    with pytest.raises(GitHubEventError, match="sender does not match"):
        parse_issue_comment_event(payload, _environment())


def test_parse_issue_comment_event_rejects_untrusted_repository_url() -> None:
    payload = _payload("issue_solve.json")
    repository = payload["repository"]
    assert isinstance(repository, dict)
    repository["html_url"] = "https://example.com/24aysh/example"

    with pytest.raises(GitHubEventError, match="repository fields"):
        parse_issue_comment_event(payload, _environment())


def test_parse_issue_comment_event_rejects_mismatched_issue_url() -> None:
    payload = _payload("issue_solve.json")
    issue = payload["issue"]
    assert isinstance(issue, dict)
    issue["html_url"] = "https://github.com/24aysh/example/issues/18"

    with pytest.raises(GitHubEventError, match="fields failed validation"):
        parse_issue_comment_event(payload, _environment())


def test_parse_issue_comment_event_rejects_mismatched_comment_url() -> None:
    payload = _payload("issue_solve.json")
    comment = payload["comment"]
    assert isinstance(comment, dict)
    comment["html_url"] = (
        "https://github.com/24aysh/example/issues/17#issuecomment-999"
    )

    with pytest.raises(GitHubEventError, match="fields failed validation"):
        parse_issue_comment_event(payload, _environment())


def test_parse_issue_comment_event_normalizes_null_issue_body() -> None:
    payload = _payload("issue_solve.json")
    issue = payload["issue"]
    assert isinstance(issue, dict)
    issue["body"] = None

    invocation = parse_issue_comment_event(payload, _environment())

    assert invocation.issue.body == ""


def test_parse_issue_comment_event_rejects_invalid_default_branch() -> None:
    payload = _payload("issue_solve.json")
    repository = payload["repository"]
    assert isinstance(repository, dict)
    repository["default_branch"] = "release..candidate"

    with pytest.raises(GitHubEventError, match="fields failed validation"):
        parse_issue_comment_event(payload, _environment())


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("GITHUB_EVENT_NAME", "push", "issue_comment event"),
        ("GITHUB_SERVER_URL", "https://example.com", "GitHub.com events only"),
        ("GITHUB_SHA", "ABC", "fields failed validation"),
        ("GITHUB_RUN_ID", "0", "positive integer"),
        ("GITHUB_RUN_ATTEMPT", "01", "positive integer"),
    ],
)
def test_parse_issue_comment_event_rejects_invalid_environment(
    name: str,
    value: str,
    message: str,
) -> None:
    environment = _environment()
    environment[name] = value

    with pytest.raises(GitHubEventError, match=message):
        parse_issue_comment_event(_payload("issue_solve.json"), environment)


def test_parse_issue_comment_event_ignores_unknown_payload_fields() -> None:
    payload = _payload("issue_solve.json")
    payload["future_field"] = {"unrecognized": True}

    invocation = parse_issue_comment_event(payload, _environment())

    assert invocation.issue.title == "Fix calculator total"


def test_parse_issue_comment_event_accepts_valid_64_character_object_id() -> None:
    environment = _environment()
    environment["GITHUB_SHA"] = "b" * 64

    invocation = parse_issue_comment_event(
        _payload("issue_solve.json"),
        environment,
    )

    assert invocation.base_sha == "b" * 64


def test_load_issue_comment_event_rejects_invalid_json(tmp_path: Path) -> None:
    event_path = tmp_path / "event.json"
    event_path.write_text("not-json", encoding="utf-8")

    with pytest.raises(GitHubEventError, match="Unable to read"):
        load_issue_comment_event(_environment(event_path))


def test_load_issue_comment_event_rejects_oversized_payload(tmp_path: Path) -> None:
    event_path = tmp_path / "event.json"
    with event_path.open("wb") as event_file:
        event_file.seek(MAX_EVENT_BYTES)
        event_file.write(b"x")

    with pytest.raises(GitHubEventError, match="exceeds"):
        load_issue_comment_event(_environment(event_path))


def _payload(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _environment(event_path: Path | None = None) -> dict[str, str]:
    values = {
        "GITHUB_EVENT_NAME": "issue_comment",
        "GITHUB_EVENT_PATH": str(event_path or FIXTURES / "issue_solve.json"),
        "GITHUB_REPOSITORY": "24aysh/example",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_RUN_ID": "9001",
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_SHA": BASE_SHA,
    }
    return values
