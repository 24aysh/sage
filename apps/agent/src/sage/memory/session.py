"""Run-scoped active context, access policy, learning, and fallback."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from uuid import UUID

from sage.errors import MemoryPolicyError
from sage.memory.canonical import canonical_digest, text_digest
from sage.memory.context import ActiveContext
from sage.memory.git_state import GitStateReader
from sage.memory.models import (
    ContextEntry,
    ContextExpansionRequest,
    ContextForest,
    DirectMaterializationRequest,
    MemoryFailure,
    MemoryMode,
    MemoryRunReport,
    MutationAuthorization,
    NodeType,
    RepositoryIdentity,
    RetrievalCandidate,
    SearchDocument,
    SemanticObject,
    SemanticState,
    SourceReadEvent,
)
from sage.memory.parsing import TreeSitterExtractor
from sage.memory.ports import SemanticSummarizer, SnapshotStore
from sage.memory.retrieval.beam import lexical_candidates, navigate
from sage.memory.retrieval.exact import exact_candidates
from sage.memory.retrieval.sparse import SQLiteSparseIndex
from sage.memory.snapshots import build_sparse_overlay
from sage.memory.summarizer import PROMPT_VERSION, build_file_semantic_object
from sage.repository import RepositoryTools
from sage.repository.paths import resolve_workspace_path, workspace_relative_path

logger = logging.getLogger(__name__)
_SEARCH_PATH = re.compile(r"^([^:\n]+):\d+:", re.MULTILINE)


@dataclass(slots=True)
class _Stats:
    reused_cards: int = 0
    created_cards: int = 0
    refreshed_cards: int = 0
    stale_cards: int = 0
    skipped_cards: int = 0
    fts_candidate_count: int = 0
    navigation_rounds: int = 0
    expansion_count: int = 0
    materialization_count: int = 0


class DisabledMemorySession:
    """No-op facade that preserves the pre-memory repository behavior."""

    def __init__(self, repository: RepositoryTools) -> None:
        self._repository = repository

    @property
    def mode(self) -> MemoryMode:
        return MemoryMode.DISABLED

    async def initial_context(self, issue_text: str) -> ContextForest:
        del issue_text
        return ContextForest()

    async def expand(self, request: ContextExpansionRequest) -> ContextForest:
        del request
        return ContextForest()

    async def materialize_dependency(
        self, request: DirectMaterializationRequest
    ) -> str:
        return self._repository.read_file(path=request.path)

    async def list_tree(self, *, path: str, max_depth: int) -> str:
        return self._repository.list_tree(path=path, max_depth=max_depth)

    async def search_text(
        self, *, query: str, path: str, max_results: int
    ) -> str:
        return self._repository.search_text(
            query=query, path=path, max_results=max_results
        )

    async def read_file(
        self, *, path: str, start_line: int, end_line: int | None
    ) -> str:
        return self._repository.read_file(
            path=path, start_line=start_line, end_line=end_line
        )

    def inspect_context(self) -> str:
        return "[]"

    def record_read(self, event: SourceReadEvent) -> None:
        del event

    def authorize_mutation(self, request: MutationAuthorization) -> None:
        del request

    def record_mutation(self, *paths: str) -> None:
        del paths

    async def finalize(self, outcome: object) -> MemoryRunReport:
        del outcome
        return MemoryRunReport(mode=MemoryMode.DISABLED)


class FallbackMemorySession(DisabledMemorySession):
    """Legacy exploration plus a sanitized report after memory failure."""

    def __init__(
        self,
        repository: RepositoryTools,
        *,
        identity: RepositoryIdentity,
        target_commit: str,
        failure: MemoryFailure,
    ) -> None:
        super().__init__(repository)
        self._identity = identity
        self._target_commit = target_commit
        self._failure = failure

    @property
    def mode(self) -> MemoryMode:
        return MemoryMode.FALLBACK

    async def finalize(self, outcome: object) -> MemoryRunReport:
        del outcome
        return MemoryRunReport(
            mode=MemoryMode.FALLBACK,
            repository_identity_digest=canonical_digest(self._identity),
            repository_display_name=self._identity.display_name,
            target_commit=self._target_commit,
            failure=self._failure,
        )


class ActiveMemorySession(DisabledMemorySession):
    """One healthy SMRT session; any engine failure transitions once."""

    def __init__(
        self,
        *,
        repository: RepositoryTools,
        identity: RepositoryIdentity,
        repository_id: UUID,
        target_commit: str,
        workspace: Path,
        git: GitStateReader,
        store: SnapshotStore,
        summarizer: SemanticSummarizer,
        extractor: TreeSitterExtractor,
        index: SQLiteSparseIndex,
        prior_documents: list[SearchDocument],
        input_snapshot_id: UUID | None,
        building_snapshot_id: UUID,
        initial_max_files: int,
        expansion_max_files: int,
        context_chars: int,
        max_file_source_chars: int,
        beam_width: int,
        max_candidates_per_round: int,
        max_navigation_rounds: int,
        parent_delta_limit: int = 3,
        parent_changed_child_limit: int = 4,
    ) -> None:
        super().__init__(repository)
        self._identity = identity
        self._repository_id = repository_id
        self._target_commit = target_commit
        self._workspace = workspace
        self._git = git
        self._store = store
        self._summarizer = summarizer
        self._extractor = extractor
        self._index = index
        self._prior_documents = prior_documents
        self._input_snapshot_id = input_snapshot_id
        self._building_snapshot_id = building_snapshot_id
        self._initial_max_files = initial_max_files
        self._expansion_max_files = expansion_max_files
        self._context_chars = context_chars
        self._max_file_source_chars = max_file_source_chars
        self._beam_width = beam_width
        self._max_candidates_per_round = max_candidates_per_round
        self._max_navigation_rounds = max_navigation_rounds
        self._parent_delta_limit = parent_delta_limit
        self._parent_changed_child_limit = parent_changed_child_limit
        self._mode = MemoryMode.HEALTHY
        self._failure: MemoryFailure | None = None
        self._context = ActiveContext(workspace)
        self._learned: dict[str, SemanticObject] = {}
        self._stats = _Stats()
        self._current_files = dict(git.list_files(target_commit))

    @property
    def mode(self) -> MemoryMode:
        return self._mode

    async def initial_context(self, issue_text: str) -> ContextForest:
        if self._mode is not MemoryMode.HEALTHY:
            return ContextForest()
        return await self._context_for_query(
            issue_text,
            max_files=self._initial_max_files,
            added_by="initial_smrt_forest",
        )

    async def expand(self, request: ContextExpansionRequest) -> ContextForest:
        if self._mode is not MemoryMode.HEALTHY:
            return ContextForest(entries=self._context.entries)
        self._stats.expansion_count += 1
        existing = set(self._context.paths)
        forest = await self._context_for_query(
            request.query,
            max_files=self._expansion_max_files,
            added_by="smrt_expansion",
            explicit_reason=request.reason,
        )
        return forest.model_copy(
            update={
                "entries": tuple(
                    entry for entry in forest.entries if entry.path not in existing
                )
            }
        )

    async def materialize_dependency(
        self, request: DirectMaterializationRequest
    ) -> str:
        if self._mode is MemoryMode.HEALTHY:
            try:
                self._context.require_dependency_provenance(request.reason)
                normalized = workspace_relative_path(self._workspace, request.path)
                if normalized not in self._current_files:
                    raise MemoryPolicyError(
                        "The dependency is not a file in the accepted target commit."
                    )
                await self._materialize(
                    RetrievalCandidate(
                        path=normalized,
                        node_type=NodeType.FILE,
                        score=0.0,
                        evidence_tier="deterministic_dependency",
                        ancestry=str(PurePosixPath(normalized).parent) or ".",
                        reason=request.reason,
                    ),
                    added_by="deterministic_dependency",
                    explicit_reason=request.reason,
                )
                if not self._context.contains(normalized):
                    raise MemoryPolicyError(
                        "The dependency could not fit in the active context budget."
                    )
            except asyncio.CancelledError:
                raise
            except MemoryPolicyError:
                raise
            except Exception as error:
                self._transition("context", "dependency", error)
        result = self._repository.read_file(path=request.path)
        self._context.record_source_read(request.path, start_line=1, end_line=None)
        return result

    async def list_tree(self, *, path: str, max_depth: int) -> str:
        normalized = workspace_relative_path(self._workspace, path)
        if self._mode is MemoryMode.HEALTHY:
            self._context.authorize_tree(normalized, max_depth=max_depth)
        return self._repository.list_tree(path=path, max_depth=max_depth)

    async def search_text(
        self, *, query: str, path: str, max_results: int
    ) -> str:
        if self._mode is MemoryMode.HEALTHY:
            self._context.authorize_search(path)
        result = self._repository.search_text(
            query=query, path=path, max_results=max_results
        )
        if self._mode is MemoryMode.HEALTHY:
            for matched_path in _SEARCH_PATH.findall(result):
                normalized = workspace_relative_path(self._workspace, matched_path)
                self._context.authorize_parent(normalized)
        return result

    async def read_file(
        self, *, path: str, start_line: int, end_line: int | None
    ) -> str:
        if self._mode is MemoryMode.HEALTHY:
            try:
                self._context.authorize_read(path)
                await self._learn_file(path)
            except asyncio.CancelledError:
                raise
            except MemoryPolicyError:
                raise
            except Exception as error:
                self._transition("learning", "read", error)
        result = self._repository.read_file(
            path=path, start_line=start_line, end_line=end_line
        )
        self._context.record_source_read(
            path, start_line=start_line, end_line=end_line
        )
        return result

    def inspect_context(self) -> str:
        return self._context.describe()

    def record_read(self, event: SourceReadEvent) -> None:
        if self._mode is MemoryMode.HEALTHY:
            self._context.record_event(event)

    def authorize_mutation(self, request: MutationAuthorization) -> None:
        if self._mode is not MemoryMode.HEALTHY:
            return
        self._context.authorize_mutation(request)

    def record_mutation(self, *paths: str) -> None:
        self._context.record_mutation(paths)

    async def finalize(self, outcome: object) -> MemoryRunReport:
        del outcome
        if self._mode is MemoryMode.HEALTHY:
            try:
                root, nodes, reused, stale = await build_sparse_overlay(
                    repository_id=self._repository_id,
                    commit_oid=self._target_commit,
                    git=self._git,
                    store=self._store,
                    summarizer=self._summarizer,
                    prior_documents=self._prior_documents,
                    learned=self._learned,
                    parent_delta_limit=self._parent_delta_limit,
                    parent_changed_child_limit=(
                        self._parent_changed_child_limit
                    ),
                )
                self._stats.reused_cards += reused
                self._stats.stale_cards += stale
                await self._store.insert_overlay_nodes(self._repository_id, nodes)
                published = await self._store.publish_snapshot(
                    snapshot_id=self._building_snapshot_id,
                    repository_id=self._repository_id,
                    root_overlay_digest=root,
                    expected_latest_id=self._input_snapshot_id,
                )
                retained = await self._store.retain_latest_five(self._repository_id)
                self._index.close()
                return self._report(
                    output_snapshot_id=published.snapshot_id,
                    snapshot_published=True,
                    retained_snapshot_count=retained,
                )
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self._transition("snapshot", "finalize", error)
        self._index.close()
        return self._report()

    async def _context_for_query(
        self,
        query: str,
        *,
        max_files: int,
        added_by: str,
        explicit_reason: str | None = None,
    ) -> ContextForest:
        try:
            documents = list(self._prior_documents)
            known_paths = {item.path for item in documents}
            documents.extend(
                SearchDocument(
                    path=path,
                    node_type=NodeType.FILE,
                    source_oid=oid,
                )
                for path, oid in self._current_files.items()
                if path not in known_paths
            )
            exact = exact_candidates(query, documents)
            sparse = self._index.search(
                query, limit=self._max_candidates_per_round
            )
            self._stats.fts_candidate_count += len(sparse)
            states = {item.path: item.semantic_state for item in documents}
            node_types = {item.path: item.node_type for item in documents}
            candidates = _merge_candidates(
                _mark_stale_hints(exact, states),
                _mark_stale_hints(
                    lexical_candidates(sparse, node_types=node_types), states
                ),
            )
            candidates = _expand_directory_candidates(
                candidates,
                documents=documents,
                max_candidates=self._max_candidates_per_round,
            )
            selected, rounds = navigate(
                candidates,
                beam_width=self._beam_width,
                max_rounds=self._max_navigation_rounds,
                max_files=max_files,
            )
            self._stats.navigation_rounds += rounds
            for candidate in selected:
                await self._materialize(
                    candidate,
                    added_by=added_by,
                    explicit_reason=explicit_reason,
                )
            return ContextForest(
                entries=self._context.entries,
                navigation_rounds=rounds,
                candidate_count=len(candidates),
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._transition("retrieval", "context", error)
            return ContextForest(entries=self._context.entries)

    async def _materialize(
        self,
        candidate: RetrievalCandidate,
        *,
        added_by: str,
        explicit_reason: str | None,
    ) -> None:
        if self._context.contains(candidate.path):
            return
        blob_oid, base_source = self._git.read_blob(
            self._target_commit, candidate.path
        )
        try:
            source = _read_workspace_source(self._workspace, candidate.path)
        except (MemoryPolicyError, OSError, UnicodeError):
            self._stats.skipped_cards += 1
            return
        remaining = self._context_chars - self._context.source_chars
        if remaining <= 0:
            return
        excerpt = source[: min(remaining, self._max_file_source_chars)]
        line_count = max(1, len(excerpt.splitlines()))
        entry = ContextEntry(
            path=candidate.path,
            base_blob_oid=blob_oid,
            workspace_content_digest=text_digest(source),
            role="primary" if self._context.file_count == 0 else "supporting",
            added_by=added_by,
            reason=explicit_reason or candidate.reason,
            evidence_tier=candidate.evidence_tier,
            materialization="full" if len(excerpt) == len(source) else "excerpt",
            included_line_ranges=((1, line_count),),
            source=excerpt,
        )
        self._context.add(
            entry,
            complete_source_digest=text_digest(source),
        )
        self._stats.materialization_count += 1
        await self._learn_file(
            candidate.path, source=base_source, source_oid=blob_oid
        )

    async def _learn_file(
        self,
        path: str,
        *,
        source: str | None = None,
        source_oid: str | None = None,
    ) -> None:
        normalized = workspace_relative_path(self._workspace, path)
        if normalized in self._learned or normalized not in self._current_files:
            return
        if source is None or source_oid is None:
            source_oid, source = self._git.read_blob(self._target_commit, normalized)
        if len(source) > self._max_file_source_chars:
            self._stats.skipped_cards += 1
            return
        structure = self._extractor.extract(normalized, source)
        if structure.parse_status == "unsupported":
            self._stats.skipped_cards += 1
            return
        existing = await self._store.find_semantic_by_source(
            self._repository_id,
            source_oid=source_oid,
            node_type=NodeType.FILE,
            summarizer_provider=self._summarizer.provider_name,
            summarizer_model=self._summarizer.model_name,
            prompt_version=PROMPT_VERSION,
            parser_version=structure.parser_version,
        )
        if existing is not None:
            self._learned[normalized] = existing
            self._stats.reused_cards += 1
            return
        payload = await self._summarizer.summarize_file(
            path=normalized, source=source, structure=structure
        )
        semantic = build_file_semantic_object(
            source_oid=source_oid,
            payload=payload,
            structure=structure,
            provider=self._summarizer.provider_name,
            model=self._summarizer.model_name,
        )
        await self._store.insert_semantic_object(self._repository_id, semantic)
        self._learned[normalized] = semantic
        self._stats.created_cards += 1

    def _transition(self, component: str, stage: str, error: Exception) -> None:
        if self._mode is not MemoryMode.HEALTHY:
            return
        self._mode = MemoryMode.FALLBACK
        self._failure = MemoryFailure(
            component=component,
            stage=stage,
            error_code=type(error).__name__[:100],
            safe_message="SMRT could not continue safely for this solve.",
            snapshot_id=self._building_snapshot_id,
            target_commit=self._target_commit,
        )
        logger.warning(
            "memory session entered fallback",
            extra={
                "component": component,
                "stage": stage,
                "error_code": type(error).__name__[:100],
            },
        )
        logger.debug("memory session failure detail", exc_info=True)
        self._prior_documents = []
        self._index.close()

    def _report(
        self,
        *,
        output_snapshot_id: UUID | None = None,
        snapshot_published: bool = False,
        retained_snapshot_count: int = 0,
    ) -> MemoryRunReport:
        return MemoryRunReport(
            mode=self._mode,
            repository_identity_digest=canonical_digest(self._identity),
            repository_display_name=self._identity.display_name,
            target_commit=self._target_commit,
            input_snapshot_id=self._input_snapshot_id,
            output_snapshot_id=output_snapshot_id,
            reused_cards=self._stats.reused_cards,
            created_cards=self._stats.created_cards,
            refreshed_cards=self._stats.refreshed_cards,
            stale_cards=self._stats.stale_cards,
            skipped_cards=self._stats.skipped_cards,
            fts_candidate_count=self._stats.fts_candidate_count,
            navigation_rounds=self._stats.navigation_rounds,
            final_file_count=self._context.file_count,
            expansion_count=self._stats.expansion_count,
            materialization_count=self._stats.materialization_count,
            summarizer_calls=int(getattr(self._summarizer, "calls", 0)),
            summarizer_input_tokens=int(
                getattr(self._summarizer, "input_tokens", 0)
            ),
            summarizer_output_tokens=int(
                getattr(self._summarizer, "output_tokens", 0)
            ),
            summarizer_latency_ms=float(
                getattr(self._summarizer, "latency_ms", 0.0)
            ),
            snapshot_published=snapshot_published,
            retained_snapshot_count=retained_snapshot_count,
            failure=self._failure,
        )


def _merge_candidates(
    first: list[RetrievalCandidate], second: list[RetrievalCandidate]
) -> list[RetrievalCandidate]:
    merged = {item.path: item for item in second}
    for item in first:
        current = merged.get(item.path)
        if current is None or item.score > current.score:
            merged[item.path] = item
    return list(merged.values())


def _mark_stale_hints(
    candidates: list[RetrievalCandidate],
    states: dict[str, SemanticState],
) -> list[RetrievalCandidate]:
    return [
        candidate.model_copy(
            update={
                "score": candidate.score - 10.0,
                "evidence_tier": f"stale_{candidate.evidence_tier}",
                "reason": "Stale memory hint; current committed source must be refreshed",
            }
        )
        if states.get(candidate.path) is SemanticState.STALE
        else candidate
        for candidate in candidates
    ]


def _expand_directory_candidates(
    candidates: list[RetrievalCandidate],
    *,
    documents: list[SearchDocument],
    max_candidates: int,
) -> list[RetrievalCandidate]:
    """Turn a semantic directory hit into bounded current file terminals."""

    by_path = {item.path: item for item in candidates}
    file_documents = [
        item for item in documents if item.node_type is NodeType.FILE
    ]
    for directory in sorted(candidates, key=lambda item: (-item.score, item.path)):
        if directory.node_type is not NodeType.DIRECTORY:
            continue
        prefix = "" if directory.path == "." else f"{directory.path.rstrip('/')}/"
        descendants = sorted(
            (item for item in file_documents if item.path.startswith(prefix)),
            key=lambda item: item.path,
        )
        for offset, descendant in enumerate(descendants[:max_candidates]):
            candidate = RetrievalCandidate(
                path=descendant.path,
                node_type=NodeType.FILE,
                score=directory.score - 1.0 - (offset / 1_000),
                evidence_tier="hierarchy_descendant",
                ancestry=directory.path,
                reason=f"Descendant of matched directory {directory.path}",
            )
            current = by_path.get(candidate.path)
            if current is None or candidate.score > current.score:
                by_path[candidate.path] = candidate
    return list(by_path.values())


def _read_workspace_source(workspace: Path, path: str) -> str:
    resolved = resolve_workspace_path(workspace, path)
    data = resolved.read_bytes()
    if b"\x00" in data[:8192]:
        raise MemoryPolicyError("Binary files cannot enter active memory context.")
    return data.decode("utf-8")
