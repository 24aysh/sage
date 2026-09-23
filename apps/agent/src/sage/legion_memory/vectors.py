"""Content-addressed embedding generations with explicit SQLite publication."""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import re
import sqlite3
import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from time import monotonic, time
from uuid import NAMESPACE_URL, uuid5

from sage.domain.embeddings import (
    EmbeddingProvider,
    MemoryVectorError,
    VectorPoint,
    VectorStatus,
    VectorStore,
    validate_vector,
)
from sage.legion_memory.store import GraphStore

logger = logging.getLogger(__name__)

_CACHE_GENERATION = "content-cache-v1"
_SNAPSHOT_RETENTION_SECONDS = 24 * 60 * 60
_CACHE_RETENTION_SECONDS = 30 * 24 * 60 * 60


@contextmanager
def memory_lock(path: Path, *, shared: bool = False) -> Iterator[None]:
    """No SQL transaction is held during network I/O; fail fast on contention."""
    if not shared:
        path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(path.suffix + ".lock").open("rb" if shared else "a") as lock:
        try:
            fcntl.flock(lock, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        except BlockingIOError:
            raise MemoryVectorError("Memory is busy in another process; retry later.") from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def bounded_text(text: str, limit: int = 7000) -> str:
    return text.encode("utf-8")[:limit].decode("utf-8", errors="ignore")


def node_text(node: dict[str, object]) -> str:
    """Same node-level representation as the reference, with a versioned prefix."""
    extra = json.loads(str(node.get("extra_json") or "{}"))
    extra = extra if isinstance(extra, dict) else {}
    name = str(node["name"])
    parent = str(node.get("parent_qualified") or "")
    parent = parent.partition("::")[2]
    split = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    split = re.sub(r"[_./-]+", " ", split)
    parts = [f"{parent}.{name}" if parent else name, name, split, str(node["kind"]).lower()]
    if parent:
        parts.append(f"in {parent}")
    for key in ("params", "return_type", "docstring"):
        value = extra.get(key)
        if isinstance(value, str):
            parts.append(" ".join(value.split())[:1500])
    parts.extend([Path(str(node["file_path"])).parent.name, str(node["language"])])
    return bounded_text(f"title: {name} | text: {' '.join(parts)}")


def vector_collection(repository_id: str, fingerprint: str) -> str:
    """Return a stable, non-identifying collection for one repository/model."""

    if not repository_id or not fingerprint:
        raise MemoryVectorError("Memory repository and embedding identity are required.")
    scope = hashlib.sha256(
        json.dumps([repository_id, fingerprint], separators=(",", ":")).encode()
    ).hexdigest()[:32]
    return f"legion_{scope}"


def cache_point_id(collection: str, qualified_name: str, text_hash: str) -> str:
    """Return the durable identity for one exact node representation."""

    return str(
        uuid5(
            NAMESPACE_URL,
            f"{collection}:cache:{qualified_name}:{text_hash}",
        )
    )


def snapshot_point_id(
    collection: str,
    generation: str,
    qualified_name: str,
) -> str:
    """Return the immutable identity for a node in one graph generation."""

    return str(
        uuid5(
            NAMESPACE_URL,
            f"{collection}:snapshot:{generation}:{qualified_name}",
        )
    )


class VectorIndex:
    """One run/CLI-owned vector capability with a bounded query cache."""

    def __init__(
        self,
        provider: EmbeddingProvider,
        store_factory: Callable[[Path, str, bool], VectorStore],
        *,
        max_nodes: int = 2000,
        deadline: float = 300,
        request_timeout: float = 30,
        min_similarity: float = 0.45,
    ) -> None:
        self.provider = provider
        self._factory = store_factory
        self._max_nodes = max_nodes
        self._deadline = deadline
        self._timeout = request_timeout
        self.min_similarity = min_similarity
        self._queries: dict[str, list[float]] = {}

    def clear_query_cache(self) -> None:
        self._queries.clear()

    def _collection(self, store: GraphStore) -> str:
        repository = store.get_metadata("repository_id") or ""
        return vector_collection(repository, self.provider.identity.fingerprint)

    def synchronize(self, store: GraphStore) -> VectorStatus:
        started = monotonic()
        eligible = embedded = reused = 0
        vectors = None
        try:
            eligible = int(store.rows("SELECT COUNT(*) AS count FROM nodes WHERE kind NOT IN ('File','Selector')")[0]["count"])
            logger.info("Legion Memory embedding preflight: %s eligible nodes; limit=%s; deadline=%ss",
                        eligible, self._max_nodes, self._deadline)
            nodes = store.rows(
                "SELECT * FROM nodes WHERE kind NOT IN ('File','Selector') "
                "ORDER BY qualified_name LIMIT ?", (self._max_nodes + 1,),
            )
            if eligible > self._max_nodes:
                raise MemoryVectorError(
                    f"Embedding node budget exceeded ({eligible} > {self._max_nodes}); "
                    "set SAGE_LEGION_EMBEDDING_MAX_NODES explicitly after reviewing cost."
                )
            identity = self.provider.identity.fingerprint
            repository = store.get_metadata("repository_id") or ""
            sha = store.get_metadata("indexed_sha") or ""
            parser = store.get_metadata("parser_version") or ""
            collection = self._collection(store)
            with store.transaction():
                store.set_metadata(f"vectors:{identity}", "{}")
            documents = [(n, node_text(n)) for n in nodes]
            hashes = [hashlib.sha256(text.encode()).hexdigest() for _, text in documents]
            content_identity = [
                (node["qualified_name"], text_hash)
                for (node, _), text_hash in zip(documents, hashes, strict=True)
            ]
            generation = hashlib.sha256(
                json.dumps(
                    [sha, parser, identity, content_identity],
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            vectors = self._factory(store.path, collection, True)
            for offset in range(0, eligible, 32):
                pending: list[tuple[str, str, str, str, str]] = []
                for (node, text), text_hash in zip(
                    documents[offset:offset + 32], hashes[offset:offset + 32], strict=True,
                ):
                    qn = str(node["qualified_name"])
                    pending.append(
                        (
                            qn,
                            text,
                            text_hash,
                            cache_point_id(collection, qn, text_hash),
                            snapshot_point_id(collection, generation, qn),
                        )
                    )
                found = vectors.get(
                    [
                        point_id
                        for _, _, _, cache_id, snapshot_id in pending
                        for point_id in (snapshot_id, cache_id)
                    ]
                )
                ready: dict[str, list[float]] = {}
                for qn, _, text_hash, cache_id, snapshot_id in pending:
                    for point_id, record_type, expected_generation in (
                        (snapshot_id, "snapshot", generation),
                        (cache_id, "cache", _CACHE_GENERATION),
                    ):
                        candidate = found.get(point_id)
                        if not _matches_point(
                            candidate,
                            qualified_name=qn,
                            text_hash=text_hash,
                            generation=expected_generation,
                            record_type=record_type,
                            repository_id=repository,
                            fingerprint=identity,
                        ):
                            continue
                        try:
                            ready[qn] = validate_vector(
                                candidate.vector,
                                self.provider.identity.dimensions,
                            )
                        except MemoryVectorError:
                            continue
                        break
                missing = [
                    (qn, text) for qn, text, _, _, _ in pending if qn not in ready
                ]
                generated: dict[str, list[float]] = {}
                embed_many = getattr(self.provider, "embed_many", None)
                if missing and embed_many is not None:
                    values = embed_many([text for _, text in missing], timeout=self._timeout,
                                        deadline=started + self._deadline)
                    if len(values) != len(missing):
                        raise MemoryVectorError("Embedding batch returned an invalid vector count.")
                    generated = dict(zip((qn for qn, _ in missing), values, strict=True))
                cache_batch: list[VectorPoint] = []
                snapshot_batch: list[VectorPoint] = []
                for qn, text, text_hash, cache_id, snapshot_id in pending:
                    remaining = self._deadline - (monotonic() - started)
                    if remaining <= 0:
                        raise MemoryVectorError("Embedding build deadline exceeded.")
                    if qn in ready:
                        vector = ready[qn]
                        reused += 1
                    else:
                        vector = validate_vector(
                            generated[qn] if qn in generated else self.provider.embed(
                                text, query=False, timeout=min(self._timeout, remaining),
                            ),
                            self.provider.identity.dimensions,
                        )
                        embedded += 1
                    cache_batch.append(
                        VectorPoint(
                            cache_id,
                            qn,
                            text_hash,
                            vector,
                            _CACHE_GENERATION,
                            record_type="cache",
                            repository_id=repository,
                            fingerprint=identity,
                        )
                    )
                    snapshot_batch.append(
                        VectorPoint(
                            snapshot_id,
                            qn,
                            text_hash,
                            vector,
                            generation,
                            repository_id=repository,
                            fingerprint=identity,
                        )
                    )
                # Refresh last_seen for reused cache/snapshot records before
                # retention cleanup. Upserts are deterministic and idempotent.
                vectors.upsert([*cache_batch, *snapshot_batch])
                verified = vectors.get([point.point_id for point in snapshot_batch])
                if any(
                    not _matches_point(
                        verified.get(point.point_id),
                        qualified_name=point.qualified_name,
                        text_hash=point.text_hash,
                        generation=generation,
                        record_type="snapshot",
                        repository_id=repository,
                        fingerprint=identity,
                    )
                    for point in snapshot_batch
                ):
                    raise MemoryVectorError("Vector upsert verification failed.")
                with store.transaction():
                    store.connection.executemany(
                        "INSERT OR REPLACE INTO vector_nodes VALUES (?, ?, ?, ?, ?)",
                        [
                            (
                                generation,
                                point.qualified_name,
                                point.text_hash,
                                point.point_id,
                                identity,
                            )
                            for point in snapshot_batch
                        ],
                    )
            if monotonic() - started >= self._deadline:
                raise MemoryVectorError("Embedding build deadline exceeded.")
            with store.transaction():
                if store.get_metadata("indexed_sha") != sha:
                    raise MemoryVectorError("Graph changed during embedding; rebuild required.")
                store.set_metadata(f"vectors:{identity}", json.dumps({
                    "sha": sha, "parser": parser, "generation": generation,
                    "count": eligible,
                }))
            # Publication is the commit point. Cleanup cannot invalidate a usable
            # generation; failed cleanup is retried on the next no-change build.
            removed = 0
            cleanup_status = "complete"
            cleanup_reason = None
            try:
                now = time()
                removed = vectors.prune(
                    keep_generation=generation,
                    snapshot_before=now - _SNAPSHOT_RETENTION_SECONDS,
                    cache_before=now - _CACHE_RETENTION_SECONDS,
                )
                with store.transaction():
                    store.connection.execute(
                        "DELETE FROM vector_nodes WHERE fingerprint=? AND generation!=?",
                        (identity, generation),
                    )
            except (MemoryVectorError, sqlite3.Error):
                cleanup_status = "pending"
                cleanup_reason = "Current vectors are ready; obsolete-vector cleanup will retry on build."
            return VectorStatus(status="ready", model=self.provider.identity.model,
                dimensions=self.provider.identity.dimensions, generation=generation,
                eligible=eligible, embedded=embedded, reused=reused,
                removed=removed, cleanup_status=cleanup_status, reason=cleanup_reason,
                duration_ms=round((monotonic() - started) * 1000, 2), usage=self.provider.usage.model_copy())
        except (MemoryVectorError, ValueError, sqlite3.Error) as error:
            return VectorStatus(status="unavailable", model=self.provider.identity.model,
                dimensions=self.provider.identity.dimensions, eligible=eligible,
                embedded=embedded, reused=reused, reason=str(error) if isinstance(error, MemoryVectorError) else "Invalid embedding metadata.",
                duration_ms=round((monotonic() - started) * 1000, 2), usage=self.provider.usage.model_copy())
        finally:
            if vectors is not None:
                vectors.close()

    def search(
        self, store: GraphStore, query: str, *, limit: int,
    ) -> tuple[list[tuple[str, float]], VectorStatus]:
        vectors = None
        started = monotonic()
        query_duration_ms = 0.0
        try:
            with memory_lock(store.path, shared=True):
                raw = store.get_metadata(f"vectors:{self.provider.identity.fingerprint}")
                manifest = json.loads(raw) if raw else {}
                if (
                    not isinstance(manifest, dict)
                    or type(manifest.get("count")) is not int
                    or manifest["count"] < 0
                    or not isinstance(manifest.get("generation"), str)
                ):
                    raise MemoryVectorError("Invalid vector publication metadata; rebuild required.")
                if (
                    manifest.get("sha") != store.get_metadata("indexed_sha")
                    or manifest.get("parser") != store.get_metadata("parser_version")
                    or not manifest.get("generation")
                ):
                    raise MemoryVectorError("Vectors are missing or stale; run the embedding-enabled memory build.")
                if manifest["count"] == 0:
                    return [], VectorStatus(
                        status="ready", model=self.provider.identity.model,
                        dimensions=self.provider.identity.dimensions,
                        generation=manifest["generation"], usage=self.provider.usage.model_copy(),
                    )
                vectors = self._factory(store.path, self._collection(store), False)
                text = "task: code retrieval | query: " + bounded_text(query)
                if text not in self._queries:
                    query_started = monotonic()
                    try:
                        value = self.provider.embed(text, query=True, timeout=self._timeout)
                    finally:
                        query_duration_ms = round((monotonic() - query_started) * 1000, 2)
                    if len(self._queries) >= 32:
                        self._queries.pop(next(iter(self._queries)))
                    self._queries[text] = validate_vector(value, self.provider.identity.dimensions)
                hits = vectors.search(self._queries[text], generation=manifest["generation"], limit=limit)
                # Payloads are locators only, never trusted nodes from another graph.
                hits = [
                    (hit.qualified_name, hit.score) for hit in hits
                    if math.isfinite(hit.score) and hit.score >= self.min_similarity
                    and hit.generation == manifest["generation"]
                    and hit.repository_id == store.get_metadata("repository_id")
                    and hit.fingerprint == self.provider.identity.fingerprint
                    and store.rows(
                        "SELECT 1 FROM vector_nodes v JOIN nodes n "
                        "ON n.qualified_name=v.qualified_name "
                        "WHERE v.generation=? AND v.fingerprint=? "
                        "AND v.qualified_name=? AND v.text_hash=?",
                        (
                            manifest["generation"],
                            self.provider.identity.fingerprint,
                            hit.qualified_name,
                            hit.text_hash,
                        ),
                    )
                ][:limit]
                return hits, VectorStatus(status="ready", model=self.provider.identity.model,
                    dimensions=self.provider.identity.dimensions, generation=manifest["generation"],
                    eligible=manifest["count"],
                    query_embedding_duration_ms=query_duration_ms,
                    duration_ms=round((monotonic() - started) * 1000, 2), usage=self.provider.usage.model_copy())
        except (MemoryVectorError, ValueError, OSError, sqlite3.Error):
            return [], VectorStatus(status="unavailable", model=self.provider.identity.model,
                dimensions=self.provider.identity.dimensions,
                reason="Vector retrieval unavailable; using lexical search. Run an embedding-enabled build to check readiness.",
                query_embedding_duration_ms=query_duration_ms,
                duration_ms=round((monotonic() - started) * 1000, 2), usage=self.provider.usage.model_copy())
        finally:
            if vectors is not None:
                vectors.close()


def _matches_point(
    point: VectorPoint | None,
    *,
    qualified_name: str,
    text_hash: str,
    generation: str,
    record_type: str,
    repository_id: str,
    fingerprint: str,
) -> bool:
    """Validate every identity field before a durable vector is reused."""

    return bool(
        point is not None
        and point.qualified_name == qualified_name
        and point.text_hash == text_hash
        and point.generation == generation
        and point.record_type == record_type
        and point.repository_id == repository_id
        and point.fingerprint == fingerprint
    )
