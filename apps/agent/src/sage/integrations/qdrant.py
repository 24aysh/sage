"""Qdrant persistence for a single memory namespace and embedding identity."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from sage.domain.embeddings import MemoryVectorError, VectorPoint, VectorUsage


class QdrantVectorStore:
    def __init__(
        self, *, collection: str, dimensions: int, usage: VectorUsage,
        path: Path | None = None, url: str | None = None, api_key: str | None = None,
        create: bool = False,
    ) -> None:
        from qdrant_client import QdrantClient, models

        self._usage = usage
        self._collection = collection
        self._remote = bool(url)
        self._client = None
        try:
            self._client = QdrantClient(
                url=url, api_key=api_key, timeout=10,
            ) if url else QdrantClient(path=str(path))
            usage.qdrant_operations += 1
            if not self._client.collection_exists(collection):
                if not create:
                    raise MemoryVectorError("Vector collection is missing; run the memory build.")
                usage.qdrant_operations += 1
                self._client.create_collection(collection, vectors_config=models.VectorParams(
                    size=dimensions, distance=models.Distance.COSINE,
                ))
            usage.qdrant_operations += 1
            config = self._client.get_collection(collection).config.params.vectors
            if not isinstance(config, models.VectorParams) or (
                config.size != dimensions or config.distance != models.Distance.COSINE
            ):
                raise MemoryVectorError("Qdrant collection identity/dimension mismatch.")
        except Exception as error:
            # SDKs use heterogeneous local/HTTP/gRPC exceptions. This adapter is
            # the boundary; suppress raw endpoints/payloads and always close.
            self.close()
            if isinstance(error, MemoryVectorError):
                raise
            raise MemoryVectorError("Qdrant unavailable or already opened by another process.") from None

    def _call(self, method: str, **arguments: Any) -> Any:
        self._usage.qdrant_operations += 1
        try:
            return getattr(self._client, method)(collection_name=self._collection, **arguments)
        except Exception:
            raise MemoryVectorError(f"Qdrant {method} failed.") from None

    def get(self, ids: Sequence[str]) -> dict[str, VectorPoint]:
        if not ids:
            return {}
        records = self._call("retrieve", ids=list(ids), with_vectors=True, with_payload=True)
        found = {}
        for item in records:
            payload = item.payload or {}
            if not isinstance(item.vector, list):
                raise MemoryVectorError("Qdrant returned invalid vector data.")
            found[str(item.id)] = VectorPoint(
                str(item.id), str(payload.get("qualified_name", "")),
                str(payload.get("text_hash", "")), item.vector,
                str(payload.get("generation", "")),
            )
        return found

    def upsert(self, points: Sequence[VectorPoint]) -> None:
        from qdrant_client import models

        self._call("upsert", wait=True, points=[models.PointStruct(
            id=p.point_id, vector=p.vector, payload={
                "qualified_name": p.qualified_name, "text_hash": p.text_hash,
                "generation": p.generation,
            },
        ) for p in points])

    def search(self, vector: list[float], *, generation: str, limit: int) -> list[tuple[str, float]]:
        from qdrant_client import models

        result = self._call("query_points", query=vector, limit=limit,
            query_filter=models.Filter(must=[models.FieldCondition(
                key="generation", match=models.MatchValue(value=generation),
            )]), search_params=models.SearchParams(exact=True) if self._remote else None,
            with_payload=True)
        return [(str((p.payload or {}).get("qualified_name", "")), p.score) for p in result.points]

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def prune(self, *, keep_generation: str) -> int:
        """Delete only obsolete points in this repository/identity collection."""
        from qdrant_client import models

        selection = models.Filter(must_not=[models.FieldCondition(
            key="generation", match=models.MatchValue(value=keep_generation),
        )])
        count = self._call("count", count_filter=selection, exact=True).count
        if count:
            self._call("delete", points_selector=models.FilterSelector(filter=selection), wait=True)
            if self._call("count", count_filter=selection, exact=True).count:
                raise MemoryVectorError("Obsolete vector cleanup was not acknowledged.")
        return count
