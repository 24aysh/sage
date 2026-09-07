"""Content-addressed embedding generations with explicit SQLite publication.

Node text composition is adapted from code-review-graph's MIT-licensed
embeddings.py (Tirth Kanani, 2026). Graph structure remains authoritative.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import re
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from time import monotonic
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
        namespace = store.get_metadata("memory_namespace")
        if not namespace:
            raise MemoryVectorError("Memory schema requires a build before embedding.")
        # Namespace plus repository prevents a foreign DB from sharing points.
        repository = str(store.get_metadata("repository_id"))
        scope = hashlib.sha256((namespace + repository).encode()).hexdigest()[:24]
        return f"legion_{scope}_{self.provider.identity.fingerprint}"

    def synchronize(self, store: GraphStore) -> VectorStatus:
        started = monotonic()
        eligible = embedded = reused = 0
        vectors = None
        try:
            nodes = store.rows(
                "SELECT * FROM nodes WHERE kind != 'File' "
                "ORDER BY qualified_name LIMIT ?", (self._max_nodes + 1,),
            )
            eligible = len(nodes)
            if eligible > self._max_nodes:
                raise MemoryVectorError("Embedding node budget exceeded; raise the explicit limit.")
            identity = self.provider.identity.fingerprint
            sha = store.get_metadata("indexed_sha")
            parser = store.get_metadata("parser_version")
            with store.transaction():
                store.set_metadata(f"vectors:{identity}", "{}")
            documents = [(n, node_text(n)) for n in nodes]
            hashes = [hashlib.sha256(text.encode()).hexdigest() for _, text in documents]
            content_identity = [
                (node["qualified_name"], text_hash)
                for (node, _), text_hash in zip(documents, hashes, strict=True)
            ]
            generation = hashlib.sha256(
                json.dumps([sha, identity, content_identity]).encode()
            ).hexdigest()
            vectors = self._factory(store.path, self._collection(store), True)
            for offset in range(0, eligible, 32):
                batch: list[VectorPoint] = []
                pending = []
                for (node, text), text_hash in zip(
                    documents[offset:offset + 32], hashes[offset:offset + 32], strict=True,
                ):
                    qn = str(node["qualified_name"])
                    old = store.rows(
                        "SELECT point_id FROM vector_nodes WHERE fingerprint=? "
                        "AND qualified_name=? AND text_hash=? "
                        "ORDER BY generation=? DESC LIMIT 1",
                        (identity, qn, text_hash, generation),
                    )
                    old_id = str(old[0]["point_id"]) if old else None
                    pending.append((qn, text, text_hash, old_id))
                cached_points = vectors.get([old_id for _, _, _, old_id in pending if old_id])
                for qn, text, text_hash, old_id in pending:
                    remaining = self._deadline - (monotonic() - started)
                    if remaining <= 0:
                        raise MemoryVectorError("Embedding build deadline exceeded.")
                    cached = cached_points.get(old_id) if old_id else None
                    if cached and cached.qualified_name == qn and cached.text_hash == text_hash:
                        try:
                            vector = validate_vector(cached.vector, self.provider.identity.dimensions)
                        except MemoryVectorError:
                            cached = None
                    else:
                        cached = None
                    if cached is not None:
                        reused += 1
                    else:
                        vector = validate_vector(
                            self.provider.embed(
                                text, query=False, timeout=min(self._timeout, remaining),
                            ),
                            self.provider.identity.dimensions,
                        )
                        embedded += 1
                    point_id = str(uuid5(NAMESPACE_URL, f"{self._collection(store)}:{generation}:{qn}"))
                    batch.append(VectorPoint(point_id, qn, text_hash, vector, generation))
                changed = [p for p in batch if cached_points.get(p.point_id) != p]
                verified = dict(cached_points)
                if changed:
                    vectors.upsert(changed)
                    verified.update(vectors.get([p.point_id for p in changed]))
                if any(p.point_id not in verified or (
                    verified[p.point_id].text_hash != p.text_hash
                    or verified[p.point_id].qualified_name != p.qualified_name
                    or verified[p.point_id].generation != generation
                ) for p in batch):
                    raise MemoryVectorError("Vector upsert verification failed.")
                with store.transaction():
                    store.connection.executemany(
                        "INSERT OR REPLACE INTO vector_nodes VALUES (?, ?, ?, ?, ?)",
                        [(generation, p.qualified_name, p.text_hash, p.point_id, identity)
                         for p in batch],
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
                removed = vectors.prune(keep_generation=generation)
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
                    value = self.provider.embed(text, query=True, timeout=self._timeout)
                    if len(self._queries) >= 32:
                        self._queries.pop(next(iter(self._queries)))
                    self._queries[text] = validate_vector(value, self.provider.identity.dimensions)
                hits = vectors.search(self._queries[text], generation=manifest["generation"], limit=limit)
                # Payloads are locators only, never trusted nodes from another graph.
                hits = [
                    (qn, score) for qn, score in hits
                    if math.isfinite(score) and score >= self.min_similarity
                    and store.rows(
                        "SELECT 1 FROM vector_nodes v JOIN nodes n "
                        "ON n.qualified_name=v.qualified_name "
                        "WHERE v.generation=? AND v.fingerprint=? AND v.qualified_name=?",
                        (manifest["generation"], self.provider.identity.fingerprint, qn),
                    )
                ][:limit]
                return hits, VectorStatus(status="ready", model=self.provider.identity.model,
                    dimensions=self.provider.identity.dimensions, generation=manifest["generation"],
                    eligible=manifest["count"],
                    duration_ms=round((monotonic() - started) * 1000, 2), usage=self.provider.usage.model_copy())
        except (MemoryVectorError, ValueError, OSError, sqlite3.Error):
            return [], VectorStatus(status="unavailable", model=self.provider.identity.model,
                dimensions=self.provider.identity.dimensions,
                reason="Vector retrieval unavailable; using lexical search. Run an embedding-enabled build to check readiness.",
                duration_ms=round((monotonic() - started) * 1000, 2), usage=self.provider.usage.model_copy())
        finally:
            if vectors is not None:
                vectors.close()
