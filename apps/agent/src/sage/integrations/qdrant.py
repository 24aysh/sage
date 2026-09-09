"""Qdrant persistence for a single memory namespace and embedding identity."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from time import time
from typing import Any

from sage.domain.embeddings import (
    MemoryVectorError,
    VectorPoint,
    VectorSearchHit,
    VectorUsage,
)


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
                try:
                    usage.qdrant_operations += 1
                    self._client.create_collection(
                        collection,
                        vectors_config=models.VectorParams(
                            size=dimensions,
                            distance=models.Distance.COSINE,
                        ),
                    )
                except Exception:
                    # Concurrent Actions runs may both observe a missing
                    # collection. Accept the winner's compatible collection.
                    usage.qdrant_operations += 1
                    if not self._client.collection_exists(collection):
                        raise
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
                record_type=(
                    "cache" if payload.get("record_type") == "cache" else "snapshot"
                ),
                repository_id=str(payload.get("repository_id", "")),
                fingerprint=str(payload.get("fingerprint", "")),
            )
        return found

    def upsert(self, points: Sequence[VectorPoint]) -> None:
        from qdrant_client import models

        seen_at = time()
        self._call("upsert", wait=True, points=[models.PointStruct(
            id=p.point_id, vector=p.vector, payload={
                "qualified_name": p.qualified_name, "text_hash": p.text_hash,
                "generation": p.generation, "record_type": p.record_type,
                "repository_id": p.repository_id,
                "fingerprint": p.fingerprint,
                "last_seen": seen_at,
            },
        ) for p in points])

    def search(
        self,
        vector: list[float],
        *,
        generation: str,
        limit: int,
    ) -> list[VectorSearchHit]:
        from qdrant_client import models

        result = self._call("query_points", query=vector, limit=limit,
            query_filter=models.Filter(must=[
                models.FieldCondition(
                    key="record_type", match=models.MatchValue(value="snapshot"),
                ),
                models.FieldCondition(
                    key="generation", match=models.MatchValue(value=generation),
                ),
            ]), search_params=models.SearchParams(exact=True) if self._remote else None,
            with_payload=True)
        return [
            VectorSearchHit(
                qualified_name=str((point.payload or {}).get("qualified_name", "")),
                text_hash=str((point.payload or {}).get("text_hash", "")),
                generation=str((point.payload or {}).get("generation", "")),
                repository_id=str((point.payload or {}).get("repository_id", "")),
                fingerprint=str((point.payload or {}).get("fingerprint", "")),
                score=point.score,
            )
            for point in result.points
        ]

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def prune(
        self,
        *,
        keep_generation: str,
        snapshot_before: float,
        cache_before: float,
    ) -> int:
        """Delete only expired immutable points from this scoped collection."""
        from qdrant_client import models

        selections = (
            models.Filter(
                must=[
                    models.FieldCondition(
                        key="record_type",
                        match=models.MatchValue(value="snapshot"),
                    ),
                    models.FieldCondition(
                        key="last_seen", range=models.Range(lt=snapshot_before),
                    ),
                ],
                must_not=[
                    models.FieldCondition(
                        key="generation",
                        match=models.MatchValue(value=keep_generation),
                    )
                ],
            ),
            models.Filter(
                must=[
                    models.FieldCondition(
                        key="record_type",
                        match=models.MatchValue(value="cache"),
                    ),
                    models.FieldCondition(
                        key="last_seen", range=models.Range(lt=cache_before),
                    ),
                ]
            ),
        )
        removed = 0
        for selection in selections:
            count = self._call(
                "count", count_filter=selection, exact=True
            ).count
            if not count:
                continue
            self._call(
                "delete",
                points_selector=models.FilterSelector(filter=selection),
                wait=True,
            )
            if self._call("count", count_filter=selection, exact=True).count:
                raise MemoryVectorError("Expired vector cleanup was not acknowledged.")
            removed += count
        return removed
