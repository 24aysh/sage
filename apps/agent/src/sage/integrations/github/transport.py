"""Bounded GitHub HTTP transport and retry metadata helpers."""

from __future__ import annotations

import re
import socket
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen

GITHUB_API_VERSION = "2026-03-10"
MAX_RESPONSE_BYTES = 2_000_000
MAX_ATTEMPTS = 3
MAX_RETRY_DELAY_SECONDS = 10.0
TRANSIENT_STATUSES = frozenset({429, 502, 503, 504})
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
        request = Request(url=url, data=body, headers=dict(headers), method=method)
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


def header(headers: Mapping[str, str], name: str) -> str | None:
    requested = name.casefold()
    for header_name, value in headers.items():
        if header_name.casefold() == requested:
            return value
    return None


def request_id(headers: Mapping[str, str]) -> str | None:
    value = header(headers, "x-github-request-id")
    if value and _REQUEST_ID_PATTERN.fullmatch(value):
        return value
    return None


def pagination_links(value: str | None) -> dict[str, int]:
    if not value:
        return {}
    pages: dict[str, int] = {}
    for url, relation in _LINK_PATTERN.findall(value):
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


def response_delay(response: HttpResponse, attempt: int) -> float:
    retry_after = header(response.headers, "retry-after")
    if retry_after is not None:
        try:
            return min(max(float(retry_after), 0.0), MAX_RETRY_DELAY_SECONDS)
        except ValueError:
            pass
    return fallback_delay(attempt)


def fallback_delay(attempt: int) -> float:
    return min(float(2**attempt), MAX_RETRY_DELAY_SECONDS)


def _normalized_headers(headers: Mapping[str, str] | None) -> dict[str, str]:
    if headers is None:
        return {}
    return {str(name).lower(): str(value) for name, value in headers.items()}


def _read_bounded(response: _ReadableResponse) -> bytes:
    return response.read(MAX_RESPONSE_BYTES + 1)
