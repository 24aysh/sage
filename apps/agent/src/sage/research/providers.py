"""Search-provider protocol and bounded Tavily adapter."""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from typing import Protocol

from sage.research.models import ProviderSearchItem, SearchRequest


class SearchProvider(Protocol):
    name: str

    async def search(self, request: SearchRequest) -> tuple[ProviderSearchItem, ...]: ...


class UnavailableSearchProvider:
    name = "unconfigured"

    async def search(self, request: SearchRequest) -> tuple[ProviderSearchItem, ...]:
        del request
        return ()


class TavilySearchProvider:
    """Small Tavily adapter implemented with the standard library."""

    name = "tavily"
    _endpoint = "https://api.tavily.com/search"

    def __init__(self, *, api_key: str, timeout_seconds: int) -> None:
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds

    async def search(self, request: SearchRequest) -> tuple[ProviderSearchItem, ...]:
        return await asyncio.to_thread(self._search_sync, request)

    def _search_sync(self, request: SearchRequest) -> tuple[ProviderSearchItem, ...]:
        payload: dict[str, object] = {
            "api_key": self._api_key,
            "query": request.query,
            "max_results": request.max_results,
            "search_depth": "advanced",
            "include_raw_content": "markdown",
            "include_answer": False,
        }
        if request.domains:
            payload["include_domains"] = list(request.domains)
        if request.recency_days is not None:
            payload["days"] = request.recency_days
        http_request = urllib.request.Request(
            self._endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(  # noqa: S310 - fixed trusted endpoint
                http_request,
                timeout=self._timeout_seconds,
            ) as response:
                body = response.read(2_000_001)
        except (OSError, urllib.error.URLError) as error:
            raise ResearchProviderError("Search provider request failed.") from error
        if len(body) > 2_000_000:
            raise ResearchProviderError("Search provider response exceeded its limit.")
        try:
            decoded = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ResearchProviderError("Search provider returned invalid JSON.") from error
        raw_results = decoded.get("results") if isinstance(decoded, dict) else None
        if not isinstance(raw_results, list):
            raise ResearchProviderError("Search provider response has no result list.")
        normalized: list[ProviderSearchItem] = []
        for item in raw_results[: request.max_results]:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "")
            if not url:
                continue
            normalized.append(
                ProviderSearchItem(
                    title=str(item.get("title") or "Untitled result")[:300],
                    url=url[:2_048],
                    snippet=str(item.get("content") or "")[:2_000],
                    content=str(
                        item.get("raw_content") or item.get("content") or ""
                    )[:50_000],
                )
            )
        return tuple(normalized)


class ResearchProviderError(RuntimeError):
    """Safe provider failure with no credentials or response bodies."""
