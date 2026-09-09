"""Explicit Gemini Embedding 2 requests, never chat-model calls."""

from __future__ import annotations

from math import isfinite
from time import monotonic, sleep
from typing import Any
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

from sage.domain.embeddings import (
    EmbeddingIdentity, MemoryVectorError, VectorUsage, validate_vector,
)


class GeminiEmbeddingProvider:
    def __init__(self, *, api_key: str | None, dimensions: int, retries: int = 1, concurrency: int = 1) -> None:
        self.identity = EmbeddingIdentity(dimensions=dimensions)
        self.usage = VectorUsage()
        self._api_key = api_key
        self._retries = retries
        if not 1 <= concurrency <= 8:
            raise ValueError("Embedding concurrency must be between 1 and 8.")
        self._concurrency = concurrency
        self._usage_lock = Lock()

    def embed_many(self, texts: list[str], *, timeout: float, deadline: float) -> list[list[float]]:
        """Independent requests, never model-2 multi-input aggregation.

        The caller supplies one checkpoint-sized batch and an absolute monotonic
        deadline. Queued work is cancelled on failure; in-flight requests retain
        their deadline and bounded retries.
        """
        def embed_one(text: str) -> list[float]:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise MemoryVectorError("Embedding build deadline exceeded.")
            return self.embed(text, query=False, timeout=min(timeout, remaining))

        executor = ThreadPoolExecutor(max_workers=self._concurrency)
        futures = []
        try:
            futures = [executor.submit(embed_one, text) for text in texts]
            return [future.result() for future in futures]
        finally:
            for future in futures:
                future.cancel()
            executor.shutdown(wait=True, cancel_futures=True)

    def embed(self, text: str, *, query: bool, timeout: float) -> list[float]:
        from google import genai
        from google.genai import errors, types
        import httpx

        if not self._api_key:
            raise MemoryVectorError("GEMINI_API_KEY is missing; embeddings unavailable.")
        deadline = monotonic() + timeout
        for attempt in range(self._retries + 1):
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise MemoryVectorError("Embedding request deadline exceeded.")
            with self._usage_lock:
                if query:
                    self.usage.query_calls += 1
                else:
                    self.usage.document_calls += 1
            try:
                # One string per request avoids model-2 multi-input aggregation.
                with genai.Client(api_key=self._api_key, http_options=types.HttpOptions(
                    timeout=max(1, int(remaining * 1000)),
                    retry_options=types.HttpRetryOptions(attempts=1),
                )) as client:
                    result = client.models.embed_content(
                        model=self.identity.model,
                        contents=text,
                        config=types.EmbedContentConfig(
                            output_dimensionality=self.identity.dimensions,
                        ),
                    )
                return self._values(result)
            except errors.APIError as error:
                if error.code not in {429, 500, 502, 503, 504} or attempt == self._retries:
                    raise MemoryVectorError(f"Gemini embedding request failed (HTTP {error.code}).") from None
                delay = min(2 ** attempt, 4)
                response = getattr(error, "response", None)
                retry_after = getattr(response, "headers", {}).get("retry-after", "")
                try:
                    requested_delay = float(retry_after)
                    if isfinite(requested_delay):
                        delay = max(delay, requested_delay)
                except (TypeError, ValueError):
                    pass
                if delay >= deadline - monotonic():
                    raise MemoryVectorError("Embedding retry exceeds request deadline.") from None
                with self._usage_lock:
                    self.usage.retries += 1
                sleep(delay)
            except (httpx.HTTPError, OSError):
                raise MemoryVectorError("Gemini embedding connection failed.") from None
        raise MemoryVectorError("Gemini embedding retries exhausted.")

    def _values(self, result: Any) -> list[float]:
        embeddings = result.embeddings
        if not embeddings or len(embeddings) != 1 or embeddings[0].values is None:
            raise MemoryVectorError("Gemini returned an invalid embedding count.")
        # embedContent does not consistently supply token usage. Do not invent it.
        return validate_vector(embeddings[0].values, self.identity.dimensions)
