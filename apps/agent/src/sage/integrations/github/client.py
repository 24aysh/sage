"""Small typed GitHub REST client built on the Python standard library."""

from __future__ import annotations

import json
import re
import socket
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol, TypeVar
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urlsplit
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from sage.errors import GitHubApiError
from sage.integrations.github.api_models import (
    GitHubBranchSnapshot,
    GitHubCommentPage,
    GitHubIssueCommentSnapshot,
    GitHubIssueSnapshot,
    GitHubPermission,
    GitHubPullRequestSnapshot,
)
from sage.integrations.github.config import GitHubSettings
from sage.integrations.github.models import GitHubRepository, validate_branch_name

GITHUB_API_VERSION = "2026-03-10"
MAX_RESPONSE_BYTES = 2_000_000
MAX_ATTEMPTS = 3
MAX_RETRY_DELAY_SECONDS = 10.0
_TRANSIENT_STATUSES = frozenset({429, 502, 503, 504})
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9:._-]{1,100}$")
_LINK_PATTERN = re.compile(r'<([^>]+)>;\s*rel="([^"]+)"')


@dataclass(frozen=True, slots=True)
class HttpResponse:
    """Bounded response returned by an injected HTTP transport."""

    status: int
    headers: Mapping[str, str]
    body: bytes


class HttpTransport(Protocol):
    """HTTP boundary used to keep normal tests offline."""

    def request(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout_seconds: int,
    ) -> HttpResponse:
        """Perform one bounded HTTP request."""


class HttpTransportError(Exception):
    """Bounded transport failure without URL, headers, or credentials."""


class _ReadableResponse(Protocol):
    def read(self, amount: int = -1) -> bytes: ...


class GitHubClient(Protocol):
    """GitHub capabilities used by gate and later workflow services."""

    def get_repository_permission(
        self,
        repository: GitHubRepository,
        actor: str,
    ) -> GitHubPermission: ...

    def get_issue(
        self,
        repository: GitHubRepository,
        issue_number: int,
    ) -> GitHubIssueSnapshot: ...

    def list_issue_comments(
        self,
        repository: GitHubRepository,
        issue_number: int,
        *,
        page: int = 1,
        per_page: int = 100,
    ) -> GitHubCommentPage: ...

    def create_issue_comment(
        self,
        repository: GitHubRepository,
        issue_number: int,
        body: str,
    ) -> GitHubIssueCommentSnapshot: ...

    def update_issue_comment(
        self,
        repository: GitHubRepository,
        comment_id: int,
        body: str,
    ) -> GitHubIssueCommentSnapshot: ...

    def get_branch(
        self,
        repository: GitHubRepository,
        branch: str,
    ) -> GitHubBranchSnapshot | None: ...

    def list_open_pull_requests(
        self,
        repository: GitHubRepository,
        *,
        head_branch: str,
        base_branch: str,
    ) -> tuple[GitHubPullRequestSnapshot, ...]: ...

    def create_pull_request(
        self,
        repository: GitHubRepository,
        *,
        title: str,
        head_branch: str,
        base_branch: str,
        body: str,
        draft: bool,
    ) -> GitHubPullRequestSnapshot: ...


class UrllibTransport:
    """urllib implementation that never returns an unbounded response."""

    def request(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout_seconds: int,
    ) -> HttpResponse:
        request = Request(
            url=url,
            data=body,
            headers=dict(headers),
            method=method,
        )
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                return HttpResponse(
                    status=response.status,
                    headers=_normalized_headers(response.headers),
                    body=_read_bounded(response),
                )
        except HTTPError as error:
            try:
                try:
                    return HttpResponse(
                        status=error.code,
                        headers=_normalized_headers(error.headers),
                        body=_read_bounded(error),
                    )
                except (TimeoutError, socket.timeout, OSError) as read_error:
                    raise HttpTransportError(
                        "GitHub API transport failed."
                    ) from read_error
            finally:
                error.close()
        except (URLError, TimeoutError, socket.timeout, OSError) as error:
            raise HttpTransportError("GitHub API transport failed.") from error


class RestGitHubClient:
    """Typed GitHub.com API operations with bounded safe retries."""

    def __init__(
        self,
        settings: GitHubSettings,
        *,
        transport: HttpTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._settings = settings
        self._transport = transport or UrllibTransport()
        self._sleep = sleep

    def get_repository_permission(
        self,
        repository: GitHubRepository,
        actor: str,
    ) -> GitHubPermission:
        response = self._request(
            method="GET",
            path=(
                f"{_repository_path(repository)}/collaborators/"
                f"{quote(actor, safe='')}/permission"
            ),
            operation="Get repository permission",
            expected_statuses={200, 404},
        )
        if response.status == 404:
            return GitHubPermission(permission="none")

        raw = _validate_model(
            _PermissionResponse,
            _decode_json(response, "Get repository permission"),
            "Get repository permission",
        )
        try:
            return GitHubPermission(
                permission=raw.permission,
                role_name=raw.role_name,
            )
        except ValidationError as error:
            raise _invalid_response("Get repository permission") from error

    def get_issue(
        self,
        repository: GitHubRepository,
        issue_number: int,
    ) -> GitHubIssueSnapshot:
        _require_positive(issue_number, "Issue number")
        operation = "Get issue"
        response = self._request(
            method="GET",
            path=f"{_repository_path(repository)}/issues/{issue_number}",
            operation=operation,
            expected_statuses={200},
        )
        raw = _validate_model(
            _IssueResponse,
            _decode_json(response, operation),
            operation,
        )
        try:
            issue = GitHubIssueSnapshot(
                number=raw.number,
                title=raw.title,
                body=raw.body or "",
                html_url=raw.html_url,
            )
        except ValidationError as error:
            raise _invalid_response(operation) from error
        expected_url = f"{repository.html_url}/issues/{issue_number}"
        if issue.number != issue_number or issue.html_url.rstrip("/") != expected_url:
            raise _invalid_response(operation)
        return issue

    def list_issue_comments(
        self,
        repository: GitHubRepository,
        issue_number: int,
        *,
        page: int = 1,
        per_page: int = 100,
    ) -> GitHubCommentPage:
        _require_positive(issue_number, "Issue number")
        _require_positive(page, "Comment page")
        if not 1 <= per_page <= 100:
            raise ValueError("Comments per page must be between 1 and 100.")

        operation = "List issue comments"
        response = self._request(
            method="GET",
            path=f"{_repository_path(repository)}/issues/{issue_number}/comments",
            query={"page": str(page), "per_page": str(per_page)},
            operation=operation,
            expected_statuses={200},
        )
        raw_comments = _validate_comments(
            _decode_json(response, operation),
            operation,
        )
        comments = tuple(
            _comment_snapshot(raw, operation=operation) for raw in raw_comments
        )
        expected_prefix = f"{repository.html_url}/issues/{issue_number}#issuecomment-"
        if any(
            item.html_url != f"{expected_prefix}{item.comment_id}"
            for item in comments
        ):
            raise _invalid_response(operation)

        links = _pagination_links(_header(response.headers, "link"))
        next_page = links.get("next")
        last_page = max(page, links.get("last", page))
        try:
            return GitHubCommentPage(
                comments=comments,
                page=page,
                next_page=next_page,
                last_page=last_page,
            )
        except ValidationError as error:
            raise _invalid_response(operation) from error

    def create_issue_comment(
        self,
        repository: GitHubRepository,
        issue_number: int,
        body: str,
    ) -> GitHubIssueCommentSnapshot:
        _require_positive(issue_number, "Issue number")
        _require_body(body, "Issue comment")
        operation = "Create issue comment"
        response = self._request(
            method="POST",
            path=f"{_repository_path(repository)}/issues/{issue_number}/comments",
            payload={"body": body},
            operation=operation,
            expected_statuses={201},
        )
        comment = _comment_snapshot(
            _validate_model(
                _CommentResponse,
                _decode_json(response, operation, ambiguous=True),
                operation,
                ambiguous=True,
            ),
            operation=operation,
            ambiguous=True,
        )
        expected_prefix = f"{repository.html_url}/issues/{issue_number}#issuecomment-"
        if comment.html_url != f"{expected_prefix}{comment.comment_id}":
            raise _invalid_response(operation, ambiguous=True)
        return comment

    def update_issue_comment(
        self,
        repository: GitHubRepository,
        comment_id: int,
        body: str,
    ) -> GitHubIssueCommentSnapshot:
        _require_positive(comment_id, "Comment ID")
        _require_body(body, "Issue comment")
        operation = "Update issue comment"
        response = self._request(
            method="PATCH",
            path=f"{_repository_path(repository)}/issues/comments/{comment_id}",
            payload={"body": body},
            operation=operation,
            expected_statuses={200},
        )
        comment = _comment_snapshot(
            _validate_model(
                _CommentResponse,
                _decode_json(response, operation),
                operation,
            ),
            operation=operation,
        )
        if (
            comment.comment_id != comment_id
            or not comment.html_url.startswith(
                f"{repository.html_url}/issues/"
            )
            or not comment.html_url.endswith(f"#issuecomment-{comment_id}")
        ):
            raise _invalid_response(operation)
        return comment

    def get_branch(
        self,
        repository: GitHubRepository,
        branch: str,
    ) -> GitHubBranchSnapshot | None:
        _require_ref(branch, "Branch")
        operation = "Get branch"
        response = self._request(
            method="GET",
            path=(
                f"{_repository_path(repository)}/branches/"
                f"{quote(branch, safe='')}"
            ),
            operation=operation,
            expected_statuses={200, 404},
        )
        if response.status == 404:
            return None

        raw = _validate_model(
            _BranchResponse,
            _decode_json(response, operation),
            operation,
        )
        try:
            branch_snapshot = GitHubBranchSnapshot(
                name=raw.name,
                sha=raw.commit.sha,
                protected=raw.protected,
            )
        except ValidationError as error:
            raise _invalid_response(operation) from error
        if branch_snapshot.name != branch:
            raise _invalid_response(operation)
        return branch_snapshot

    def list_open_pull_requests(
        self,
        repository: GitHubRepository,
        *,
        head_branch: str,
        base_branch: str,
    ) -> tuple[GitHubPullRequestSnapshot, ...]:
        _require_ref(head_branch, "Head branch")
        _require_ref(base_branch, "Base branch")
        operation = "List open pull requests"
        response = self._request(
            method="GET",
            path=f"{_repository_path(repository)}/pulls",
            query={
                "base": base_branch,
                "head": f"{repository.owner}:{head_branch}",
                "state": "open",
            },
            operation=operation,
            expected_statuses={200},
        )
        raw_pull_requests = _validate_pull_requests(
            _decode_json(response, operation),
            operation,
        )
        expected_head_label = f"{repository.owner}:{head_branch}"
        if any(
            pull_request.head.label != expected_head_label
            for pull_request in raw_pull_requests
        ):
            raise _invalid_response(operation)
        pull_requests = tuple(
            _pull_request_snapshot(raw, operation=operation)
            for raw in raw_pull_requests
        )
        expected_prefix = f"{repository.html_url}/pull/"
        if any(
            item.state != "open"
            or item.head_ref != head_branch
            or item.base_ref != base_branch
            or item.html_url != f"{expected_prefix}{item.number}"
            for item in pull_requests
        ):
            raise _invalid_response(operation)
        return pull_requests

    def create_pull_request(
        self,
        repository: GitHubRepository,
        *,
        title: str,
        head_branch: str,
        base_branch: str,
        body: str,
        draft: bool,
    ) -> GitHubPullRequestSnapshot:
        _require_title(title)
        _require_ref(head_branch, "Head branch")
        _require_ref(base_branch, "Base branch")
        _require_body(body, "Pull Request body")
        operation = "Create pull request"
        response = self._request(
            method="POST",
            path=f"{_repository_path(repository)}/pulls",
            payload={
                "base": base_branch,
                "body": body,
                "draft": draft,
                "head": head_branch,
                "title": title,
            },
            operation=operation,
            expected_statuses={201},
            ambiguous_statuses={422},
        )
        raw_pull_request = _validate_model(
            _PullRequestResponse,
            _decode_json(response, operation, ambiguous=True),
            operation,
            ambiguous=True,
        )
        pull_request = _pull_request_snapshot(
            raw_pull_request,
            operation=operation,
            ambiguous=True,
        )
        if (
            raw_pull_request.head.label
            != f"{repository.owner}:{head_branch}"
            or pull_request.head_ref != head_branch
            or pull_request.base_ref != base_branch
            or pull_request.html_url
            != f"{repository.html_url}/pull/{pull_request.number}"
        ):
            raise _invalid_response(operation, ambiguous=True)
        return pull_request

    def _request(
        self,
        *,
        method: str,
        path: str,
        operation: str,
        expected_statuses: set[int],
        query: Mapping[str, str] | None = None,
        payload: Mapping[str, object] | None = None,
        ambiguous_statuses: set[int] | None = None,
    ) -> HttpResponse:
        query_string = f"?{urlencode(query)}" if query else ""
        url = f"{self._settings.api_url}{path}{query_string}"
        request_body = (
            json.dumps(payload, separators=(",", ":")).encode("utf-8")
            if payload is not None
            else None
        )
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._settings.github_token}",
            "User-Agent": "sage/1.0",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
        }
        if request_body is not None:
            headers["Content-Type"] = "application/json"

        retryable = method in {"GET", "PATCH"}
        attempts = MAX_ATTEMPTS if retryable else 1
        for attempt in range(attempts):
            try:
                response = self._transport.request(
                    method=method,
                    url=url,
                    headers=headers,
                    body=request_body,
                    timeout_seconds=self._settings.api_timeout_seconds,
                )
            except HttpTransportError as error:
                if retryable and attempt + 1 < attempts:
                    self._sleep(_fallback_delay(attempt))
                    continue
                raise GitHubApiError(
                    f"{operation} failed because the GitHub API was unreachable.",
                    ambiguous=method == "POST",
                ) from error

            if len(response.body) > MAX_RESPONSE_BYTES:
                raise GitHubApiError(
                    f"{operation} returned an oversized response.",
                    status_code=response.status,
                    request_id=_request_id(response.headers),
                    ambiguous=method == "POST",
                )
            if response.status in expected_statuses:
                return response
            if (
                retryable
                and response.status in _TRANSIENT_STATUSES
                and attempt + 1 < attempts
            ):
                self._sleep(_response_delay(response, attempt))
                continue

            request_id = _request_id(response.headers)
            request_suffix = f" (request {request_id})" if request_id else ""
            raise GitHubApiError(
                f"{operation} failed with HTTP {response.status}{request_suffix}.",
                status_code=response.status,
                request_id=request_id,
                ambiguous=(
                    method == "POST"
                    and (
                        response.status >= 500
                        or response.status in (ambiguous_statuses or set())
                    )
                ),
            )

        raise AssertionError("GitHub request loop terminated unexpectedly.")


class _PermissionResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    permission: str
    role_name: str | None = None


class _IssueResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    number: int
    title: str
    body: str | None = None
    html_url: str


class _UserResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    login: str


class _CommentResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    body: str | None = None
    created_at: str
    html_url: str
    user: _UserResponse | None = None


class _CommitResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    sha: str


class _BranchResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    commit: _CommitResponse
    protected: bool


class _PullRequestRefResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ref: str
    label: str


class _PullRequestResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    number: int
    html_url: str
    state: str
    draft: bool = False
    head: _PullRequestRefResponse
    base: _PullRequestRefResponse


_COMMENTS_ADAPTER = TypeAdapter(list[_CommentResponse])
_PULL_REQUESTS_ADAPTER = TypeAdapter(list[_PullRequestResponse])
_ModelType = TypeVar("_ModelType", bound=BaseModel)


def _validate_model(
    model: type[_ModelType],
    payload: object,
    operation: str,
    *,
    ambiguous: bool = False,
) -> _ModelType:
    try:
        return model.model_validate(payload)
    except ValidationError as error:
        raise _invalid_response(operation, ambiguous=ambiguous) from error


def _validate_comments(payload: object, operation: str) -> list[_CommentResponse]:
    try:
        return _COMMENTS_ADAPTER.validate_python(payload)
    except ValidationError as error:
        raise _invalid_response(operation) from error


def _validate_pull_requests(
    payload: object,
    operation: str,
) -> list[_PullRequestResponse]:
    try:
        return _PULL_REQUESTS_ADAPTER.validate_python(payload)
    except ValidationError as error:
        raise _invalid_response(operation) from error


def _comment_snapshot(
    raw: _CommentResponse,
    *,
    operation: str,
    ambiguous: bool = False,
) -> GitHubIssueCommentSnapshot:
    try:
        return GitHubIssueCommentSnapshot(
            comment_id=raw.id,
            body=raw.body or "",
            author_login=raw.user.login if raw.user else "ghost",
            created_at=raw.created_at,
            html_url=raw.html_url,
        )
    except ValidationError as error:
        raise _invalid_response(operation, ambiguous=ambiguous) from error


def _pull_request_snapshot(
    raw: _PullRequestResponse,
    *,
    operation: str,
    ambiguous: bool = False,
) -> GitHubPullRequestSnapshot:
    try:
        return GitHubPullRequestSnapshot(
            number=raw.number,
            html_url=raw.html_url,
            state=raw.state,
            draft=raw.draft,
            head_ref=raw.head.ref,
            base_ref=raw.base.ref,
        )
    except ValidationError as error:
        raise _invalid_response(operation, ambiguous=ambiguous) from error


def _decode_json(
    response: HttpResponse,
    operation: str,
    *,
    ambiguous: bool = False,
) -> object:
    try:
        return json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _invalid_response(operation, ambiguous=ambiguous) from error


def _invalid_response(
    operation: str,
    *,
    ambiguous: bool = False,
) -> GitHubApiError:
    return GitHubApiError(
        f"{operation} returned an invalid response.",
        ambiguous=ambiguous,
    )


def _repository_path(repository: GitHubRepository) -> str:
    owner = quote(repository.owner, safe="")
    name = quote(repository.name, safe="")
    return f"/repos/{owner}/{name}"


def _require_positive(value: int, label: str) -> None:
    if value < 1:
        raise ValueError(f"{label} must be positive.")


def _require_body(value: str, label: str) -> None:
    if not value or len(value) > 65_536:
        raise ValueError(f"{label} must contain between 1 and 65536 characters.")


def _require_title(value: str) -> None:
    if not value or len(value) > 256 or "\n" in value or "\r" in value:
        raise ValueError(
            "Pull Request title must be a single line of 1-256 characters."
        )


def _require_ref(value: str, label: str) -> None:
    try:
        validate_branch_name(value)
    except ValueError as error:
        raise ValueError(f"{label} is invalid.") from error


def _normalized_headers(
    headers: Mapping[str, str] | None,
) -> dict[str, str]:
    if headers is None:
        return {}
    return {str(name).lower(): str(value) for name, value in headers.items()}


def _read_bounded(response: _ReadableResponse) -> bytes:
    return response.read(MAX_RESPONSE_BYTES + 1)


def _header(headers: Mapping[str, str], name: str) -> str | None:
    requested = name.casefold()
    for header_name, value in headers.items():
        if header_name.casefold() == requested:
            return value
    return None


def _request_id(headers: Mapping[str, str]) -> str | None:
    value = _header(headers, "x-github-request-id")
    if value and _REQUEST_ID_PATTERN.fullmatch(value):
        return value
    return None


def _pagination_links(header: str | None) -> dict[str, int]:
    if not header:
        return {}
    pages: dict[str, int] = {}
    for url, relation in _LINK_PATTERN.findall(header):
        values = parse_qs(urlsplit(url).query).get("page", [])
        if len(values) != 1:
            continue
        try:
            page = int(values[0])
        except ValueError:
            continue
        if page > 0:
            pages[relation] = page
    return pages


def _response_delay(response: HttpResponse, attempt: int) -> float:
    retry_after = _header(response.headers, "retry-after")
    if retry_after is not None:
        try:
            return min(max(float(retry_after), 0.0), MAX_RETRY_DELAY_SECONDS)
        except ValueError:
            pass
    return _fallback_delay(attempt)


def _fallback_delay(attempt: int) -> float:
    return min(float(2**attempt), MAX_RETRY_DELAY_SECONDS)
