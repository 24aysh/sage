import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlsplit

import pytest

from sage.errors import GitHubApiError
from sage.integrations.github.api_models import GitHubPermission
from sage.integrations.github.client import (
    GITHUB_API_VERSION,
    MAX_RESPONSE_BYTES,
    HttpResponse,
    HttpTransportError,
    RestGitHubClient,
)
from sage.integrations.github.config import GitHubSettings
from sage.integrations.github.models import GitHubRepository


@dataclass(frozen=True, slots=True)
class RecordedRequest:
    method: str
    url: str
    headers: Mapping[str, str]
    body: bytes | None
    timeout_seconds: int


@dataclass(slots=True)
class FakeTransport:
    responses: list[HttpResponse | Exception]
    requests: list[RecordedRequest] = field(default_factory=list)

    def request(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout_seconds: int,
    ) -> HttpResponse:
        self.requests.append(
            RecordedRequest(method, url, headers, body, timeout_seconds)
        )
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_get_permission_uses_trusted_headers_and_encoded_actor() -> None:
    transport = FakeTransport([_response(200, {"permission": "write"})])
    client = _client(transport)

    permission = client.get_repository_permission(_repository(), "sage user")

    assert permission == GitHubPermission(permission="write")
    request = transport.requests[0]
    assert request.method == "GET"
    assert request.url == (
        "https://api.github.com/repos/24aysh/example/"
        "collaborators/sage%20user/permission"
    )
    assert request.headers["Authorization"] == "Bearer secret-token"
    assert request.headers["X-GitHub-Api-Version"] == GITHUB_API_VERSION
    assert request.timeout_seconds == 17


def test_missing_collaborator_is_normalized_to_no_permission() -> None:
    client = _client(FakeTransport([_response(404, {"message": "Not Found"})]))

    permission = client.get_repository_permission(_repository(), "unknown")

    assert permission.permission == "none"


def test_get_retries_transient_responses_with_bounded_retry_after() -> None:
    sleeps: list[float] = []
    transport = FakeTransport(
        [
            _response(503, {}, headers={"Retry-After": "500"}),
            _response(502, {}),
            _response(200, {"permission": "admin"}),
        ]
    )
    client = _client(transport, sleep=sleeps.append)

    permission = client.get_repository_permission(_repository(), "maintainer")

    assert permission.permission == "admin"
    assert len(transport.requests) == 3
    assert sleeps == [10.0, 2.0]


def test_get_retries_transport_failure_but_does_not_leak_token() -> None:
    transport = FakeTransport(
        [
            HttpTransportError("unsafe transport detail secret-token"),
            HttpTransportError("unsafe transport detail secret-token"),
            HttpTransportError("unsafe transport detail secret-token"),
        ]
    )
    client = _client(transport, sleep=lambda _: None)

    with pytest.raises(GitHubApiError) as raised:
        client.get_repository_permission(_repository(), "maintainer")

    assert len(transport.requests) == 3
    assert "unreachable" in str(raised.value)
    assert "secret-token" not in str(raised.value)
    assert raised.value.ambiguous is False


def test_api_error_includes_only_safe_status_and_request_id() -> None:
    transport = FakeTransport(
        [
            _response(
                403,
                {"message": "secret-token should not be displayed"},
                headers={"X-GitHub-Request-Id": "ABCD:1234"},
            )
        ]
    )
    client = _client(transport)

    with pytest.raises(GitHubApiError) as raised:
        client.get_repository_permission(_repository(), "maintainer")

    error = raised.value
    assert str(error) == (
        "Get repository permission failed with HTTP 403 (request ABCD:1234)."
    )
    assert error.status_code == 403
    assert error.request_id == "ABCD:1234"
    assert "secret-token" not in str(error)


def test_post_transport_failure_is_not_blindly_retried() -> None:
    transport = FakeTransport([HttpTransportError("connection closed")])
    client = _client(transport)

    with pytest.raises(GitHubApiError) as raised:
        client.create_issue_comment(_repository(), 17, "Working")

    assert len(transport.requests) == 1
    assert raised.value.ambiguous is True


def test_create_pull_request_422_is_ambiguous_and_not_retried() -> None:
    transport = FakeTransport([_response(422, {"message": "already exists"})])
    client = _client(transport)

    with pytest.raises(GitHubApiError) as raised:
        client.create_pull_request(
            _repository(),
            title="Sage candidate",
            head_branch="sage/issue-17",
            base_branch="main",
            body="Automated candidate",
            draft=True,
        )

    assert len(transport.requests) == 1
    assert raised.value.status_code == 422
    assert raised.value.ambiguous is True


def test_patch_retries_a_transient_response_with_the_same_body() -> None:
    transport = FakeTransport(
        [
            _response(503, {}),
            _response(200, _comment_payload(103)),
        ]
    )
    client = _client(transport)

    comment = client.update_issue_comment(_repository(), 103, "Working")

    assert comment.comment_id == 103
    assert len(transport.requests) == 2
    assert transport.requests[0].body == transport.requests[1].body


def test_oversized_response_is_rejected_without_retry() -> None:
    transport = FakeTransport(
        [HttpResponse(200, {}, b"x" * (MAX_RESPONSE_BYTES + 1))]
    )
    client = _client(transport)

    with pytest.raises(GitHubApiError, match="oversized response"):
        client.get_repository_permission(_repository(), "maintainer")

    assert len(transport.requests) == 1


def test_issue_and_comment_operations_validate_and_normalize_responses() -> None:
    transport = FakeTransport(
        [
            _response(
                200,
                {
                    "number": 17,
                    "title": "Fix calculator total",
                    "body": None,
                    "html_url": "https://github.com/24aysh/example/issues/17",
                },
            ),
            _response(
                200,
                [_comment_payload(101), _comment_payload(102)],
                headers={
                    "Link": (
                        '<https://api.github.com/repos/24aysh/example/issues/17/'
                        'comments?page=3&per_page=2>; rel="next", '
                        '<https://api.github.com/repos/24aysh/example/issues/17/'
                        'comments?page=4&per_page=2>; rel="last"'
                    )
                },
            ),
            _response(201, _comment_payload(103)),
            _response(200, _comment_payload(103)),
        ]
    )
    client = _client(transport)

    issue = client.get_issue(_repository(), 17)
    page = client.list_issue_comments(_repository(), 17, page=2, per_page=2)
    created = client.create_issue_comment(_repository(), 17, "Accepted")
    updated = client.update_issue_comment(_repository(), 103, "Working")

    assert issue.body == ""
    assert [comment.comment_id for comment in page.comments] == [101, 102]
    assert (page.page, page.next_page, page.last_page) == (2, 3, 4)
    assert created.comment_id == updated.comment_id == 103
    list_query = parse_qs(urlsplit(transport.requests[1].url).query)
    assert list_query == {"page": ["2"], "per_page": ["2"]}
    assert _request_json(transport.requests[2]) == {"body": "Accepted"}
    assert transport.requests[3].method == "PATCH"
    assert _request_json(transport.requests[3]) == {"body": "Working"}


def test_branch_and_pull_request_operations_encode_and_validate_refs() -> None:
    transport = FakeTransport(
        [
            _response(
                200,
                {
                    "name": "sage/issue-17",
                    "commit": {"sha": "a" * 40},
                    "protected": False,
                },
            ),
            _response(404, {"message": "Not Found"}),
            _response(200, [_pull_request_payload(21)]),
            _response(201, _pull_request_payload(22)),
        ]
    )
    client = _client(transport)

    branch = client.get_branch(_repository(), "sage/issue-17")
    missing = client.get_branch(_repository(), "sage/issue-18")
    existing = client.list_open_pull_requests(
        _repository(),
        head_branch="sage/issue-17",
        base_branch="main",
    )
    created = client.create_pull_request(
        _repository(),
        title="Sage: fix issue #17",
        head_branch="sage/issue-17",
        base_branch="main",
        body="Automated candidate",
        draft=True,
    )

    assert branch is not None and branch.sha == "a" * 40
    assert missing is None
    assert transport.requests[0].url.endswith("/branches/sage%2Fissue-17")
    query = parse_qs(urlsplit(transport.requests[2].url).query)
    assert query == {
        "base": ["main"],
        "head": ["24aysh:sage/issue-17"],
        "state": ["open"],
    }
    assert existing[0].number == 21
    assert created.number == 22
    assert _request_json(transport.requests[3]) == {
        "base": "main",
        "body": "Automated candidate",
        "draft": True,
        "head": "sage/issue-17",
        "title": "Sage: fix issue #17",
    }


@pytest.mark.parametrize(
    "payload",
    [
        b"not-json",
        json.dumps({"permission": "superuser"}).encode(),
    ],
)
def test_malformed_permission_response_is_rejected(payload: bytes) -> None:
    client = _client(FakeTransport([HttpResponse(200, {}, payload)]))

    with pytest.raises(GitHubApiError, match="invalid response"):
        client.get_repository_permission(_repository(), "maintainer")


def test_mismatched_issue_url_is_rejected() -> None:
    client = _client(
        FakeTransport(
            [
                _response(
                    200,
                    {
                        "number": 17,
                        "title": "Issue",
                        "body": "Body",
                        "html_url": "https://github.com/other/example/issues/17",
                    },
                )
            ]
        )
    )

    with pytest.raises(GitHubApiError, match="invalid response"):
        client.get_issue(_repository(), 17)


def test_pull_request_from_an_unexpected_head_repository_is_rejected() -> None:
    payload = _pull_request_payload(21)
    head = payload["head"]
    assert isinstance(head, dict)
    head["label"] = "other:sage/issue-17"
    client = _client(FakeTransport([_response(200, [payload])]))

    with pytest.raises(GitHubApiError, match="invalid response"):
        client.list_open_pull_requests(
            _repository(),
            head_branch="sage/issue-17",
            base_branch="main",
        )


def test_comment_with_invalid_author_login_is_rejected() -> None:
    payload = _comment_payload(101)
    user = payload["user"]
    assert isinstance(user, dict)
    user["login"] = "unsafe\nheading"
    client = _client(FakeTransport([_response(200, [payload])]))

    with pytest.raises(GitHubApiError, match="invalid response"):
        client.list_issue_comments(_repository(), 17)


def _client(
    transport: FakeTransport,
    *,
    sleep: Callable[[float], None] = lambda _: None,
) -> RestGitHubClient:
    settings = GitHubSettings(
        github_token="secret-token",
        api_timeout_seconds=17,
    )
    return RestGitHubClient(settings, transport=transport, sleep=sleep)


def _repository() -> GitHubRepository:
    return GitHubRepository(
        owner="24aysh",
        name="example",
        repository_id=123,
        html_url="https://github.com/24aysh/example",
    )


def _response(
    status: int,
    payload: object,
    *,
    headers: Mapping[str, str] | None = None,
) -> HttpResponse:
    return HttpResponse(
        status=status,
        headers=headers or {},
        body=json.dumps(payload).encode("utf-8"),
    )


def _comment_payload(comment_id: int) -> dict[str, object]:
    return {
        "id": comment_id,
        "body": "Comment",
        "created_at": "2026-08-19T10:00:00Z",
        "html_url": (
            "https://github.com/24aysh/example/issues/17"
            f"#issuecomment-{comment_id}"
        ),
        "user": {"login": "github-actions[bot]"},
    }


def _pull_request_payload(number: int) -> dict[str, object]:
    return {
        "number": number,
        "html_url": f"https://github.com/24aysh/example/pull/{number}",
        "state": "open",
        "draft": True,
        "head": {"ref": "sage/issue-17", "label": "24aysh:sage/issue-17"},
        "base": {"ref": "main", "label": "24aysh:main"},
    }


def _request_json(request: RecordedRequest) -> object:
    assert request.body is not None
    return json.loads(request.body.decode("utf-8"))
