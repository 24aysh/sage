"""Sparse copy-on-write overlay construction and bottom-up refresh."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath
from uuid import UUID

from sage.memory.canonical import canonical_digest
from sage.memory.git_state import GitStateReader
from sage.memory.models import (
    CoverageState,
    DirectorySemanticPayload,
    FileSemanticPayload,
    NodeType,
    OverlayNode,
    SearchDocument,
    SemanticObject,
    SemanticState,
)
from sage.memory.ports import SemanticSummarizer, SnapshotStore
from sage.memory.summarizer import PROMPT_VERSION, build_directory_semantic_object


async def build_sparse_overlay(
    *,
    repository_id: UUID,
    commit_oid: str,
    git: GitStateReader,
    store: SnapshotStore,
    summarizer: SemanticSummarizer,
    prior_documents: Sequence[SearchDocument],
    learned: Mapping[str, SemanticObject],
    parent_delta_limit: int,
    parent_changed_child_limit: int,
) -> tuple[str, list[OverlayNode], int, int]:
    """Build only paths reached previously or during the current solve."""

    current_files = dict(git.list_files(commit_oid))
    prior_by_path = _carry_unambiguous_renames(
        prior_documents, current_files=current_files
    )
    known_paths = sorted(
        set(learned).union(
            path
            for path, item in prior_by_path.items()
            if item.node_type is NodeType.FILE and path in current_files
        ),
        key=lambda item: item.encode("utf-8"),
    )
    nodes: dict[str, OverlayNode] = {}
    semantic_payloads: dict[str, FileSemanticPayload | DirectorySemanticPayload] = {
        item.path: _payload(item)
        for item in prior_documents
        if item.semantic_digest is not None and item.summary
    }
    semantic_payload_digests = {
        item.path: item.payload_digest
        for item in prior_documents
        if item.payload_digest is not None
    }
    reused = 0
    stale = 0

    for path in known_paths:
        source_oid = current_files[path]
        semantic = learned.get(path)
        prior = prior_by_path.get(path)
        if semantic is not None:
            state = SemanticState.VALID
            semantic_digest = semantic.semantic_digest
            stale_hint = None
            semantic_payloads[path] = semantic.semantic_payload
            semantic_payload_digests[path] = semantic.payload_digest
        elif (
            prior
            and prior.semantic_state is SemanticState.VALID
            and prior.source_oid == source_oid
            and prior.semantic_digest
        ):
            state = SemanticState.VALID
            semantic_digest = prior.semantic_digest
            stale_hint = None
            if prior.payload_digest is not None:
                semantic_payload_digests[path] = prior.payload_digest
            reused += 1
        else:
            stale_candidate = (
                (prior.stale_hint_digest or prior.semantic_digest)
                if prior
                else None
            )
            state = SemanticState.STALE if stale_candidate else SemanticState.MISSING
            semantic_digest = None
            stale_hint = stale_candidate
            stale += int(state is SemanticState.STALE)
        nodes[path] = _overlay(
            node_type=NodeType.FILE,
            source_oid=source_oid,
            semantic_digest=semantic_digest,
            stale_hint_digest=stale_hint,
            semantic_state=state,
        )

    directories = {"."}
    for path in known_paths:
        parent = PurePosixPath(path).parent
        while str(parent) not in {"", "."}:
            directories.add(str(parent))
            parent = parent.parent
    for directory in sorted(
        directories,
        key=lambda item: (-len(PurePosixPath(item).parts), item.encode("utf-8")),
    ):
        child_paths = _immediate_children(directory, tuple(nodes))
        children = tuple(
            sorted(
                ((PurePosixPath(path).name, nodes[path].overlay_digest) for path in child_paths),
                key=lambda item: item[0].encode("utf-8"),
            )
        )
        child_cards = [
            (PurePosixPath(path).name, semantic_payloads[path])
            for path in child_paths
            if path in semantic_payloads and nodes[path].semantic_state is SemanticState.VALID
        ]
        source_oid = git.object_oid(commit_oid, directory)
        semantic_digest: str | None = None
        if child_cards:
            dependencies = tuple(
                sorted(
                    (
                        (PurePosixPath(path).name, nodes[path].semantic_digest)
                        for path in child_paths
                        if nodes[path].semantic_digest is not None
                    ),
                    key=lambda item: item[0].encode("utf-8"),
                )
            )
            semantic = await store.find_semantic_by_source(
                repository_id,
                source_oid=source_oid,
                node_type=NodeType.DIRECTORY,
                summarizer_provider=summarizer.provider_name,
                summarizer_model=summarizer.model_name,
                prompt_version=PROMPT_VERSION,
                parser_version=None,
            )
            prior = prior_by_path.get(directory)
            if semantic is not None and semantic.derived_from == dependencies:
                reused += 1
            else:
                prior_payload = (
                    _payload(prior)
                    if prior is not None
                    and prior.node_type is NodeType.DIRECTORY
                    and prior.semantic_state is SemanticState.VALID
                    and prior.semantic_digest is not None
                    else None
                )
                if (
                    isinstance(prior_payload, DirectorySemanticPayload)
                    and prior is not None
                    and _child_meaning_is_unchanged(
                        directory=directory,
                        previous_dependencies=dict(prior.derived_from),
                        current_dependencies=dict(dependencies),
                        prior_documents=prior_by_path,
                        current_payload_digests=semantic_payload_digests,
                    )
                ):
                    payload = prior_payload
                    generation_mode = prior.generation_mode
                    delta_depth = prior.delta_depth
                    reused += 1
                else:
                    previous_dependencies = (
                        dict(prior.derived_from) if prior is not None else {}
                    )
                    current_dependencies = dict(dependencies)
                    changed_names = {
                        name
                        for name in previous_dependencies.keys()
                        | current_dependencies.keys()
                        if previous_dependencies.get(name)
                        != current_dependencies.get(name)
                    }
                    use_delta = (
                        isinstance(prior_payload, DirectorySemanticPayload)
                        and prior is not None
                        and len(changed_names) <= parent_changed_child_limit
                        and prior.delta_depth < parent_delta_limit
                    )
                    if use_delta:
                        payload = await summarizer.summarize_directory_delta(
                            path=directory,
                            previous=prior_payload,
                            changed_children=[
                                (name, child_payload)
                                for name, child_payload in child_cards
                                if name in changed_names
                            ],
                            removed_children=sorted(
                                previous_dependencies.keys()
                                - current_dependencies.keys(),
                                key=lambda item: item.encode("utf-8"),
                            ),
                        )
                        generation_mode = "delta"
                        delta_depth = prior.delta_depth + 1
                    else:
                        payload = await summarizer.summarize_directory(
                            path=directory, children=child_cards
                        )
                        generation_mode = "full"
                        delta_depth = 0
                semantic = build_directory_semantic_object(
                    source_oid=source_oid,
                    payload=payload,
                    children=dependencies,
                    provider=summarizer.provider_name,
                    model=summarizer.model_name,
                    generation_mode=generation_mode,
                    delta_depth=delta_depth,
                )
                await store.insert_semantic_object(repository_id, semantic)
            semantic_payloads[directory] = semantic.semantic_payload
            semantic_payload_digests[directory] = semantic.payload_digest
            semantic_digest = semantic.semantic_digest
        nodes[directory] = _overlay(
            node_type=NodeType.DIRECTORY,
            source_oid=source_oid,
            semantic_digest=semantic_digest,
            stale_hint_digest=None,
            semantic_state=(
                SemanticState.VALID if semantic_digest else SemanticState.MISSING
            ),
            coverage_state=CoverageState.PARTIAL,
            children=children,
        )

    ordered = sorted(
        nodes.items(),
        key=lambda item: (-len(PurePosixPath(item[0]).parts), item[0].encode("utf-8")),
    )
    return nodes["."].overlay_digest, [value for _, value in ordered], reused, stale


def _payload(item: SearchDocument) -> FileSemanticPayload | DirectorySemanticPayload:
    if item.node_type is NodeType.FILE:
        return FileSemanticPayload(
            summary=item.summary,
            responsibilities=list(item.responsibilities),
            concepts=list(item.concepts),
        )
    return DirectorySemanticPayload(
        summary=item.summary,
        responsibilities=list(item.responsibilities),
        not_responsible_for=list(item.not_responsible_for),
        concepts=list(item.concepts),
    )


def _overlay(
    *,
    node_type: NodeType,
    source_oid: str,
    semantic_digest: str | None,
    stale_hint_digest: str | None,
    semantic_state: SemanticState,
    coverage_state: CoverageState | None = None,
    children: tuple[tuple[str, str], ...] = (),
) -> OverlayNode:
    envelope = {
        "node_type": node_type.value,
        "source_oid": source_oid,
        "semantic_digest": semantic_digest,
        "stale_hint_digest": stale_hint_digest,
        "semantic_state": semantic_state.value,
        "coverage_state": coverage_state.value if coverage_state else None,
        "children": children,
    }
    return OverlayNode(
        overlay_digest=canonical_digest(envelope),
        node_type=node_type,
        source_oid=source_oid,
        semantic_digest=semantic_digest,
        stale_hint_digest=stale_hint_digest,
        semantic_state=semantic_state,
        coverage_state=coverage_state,
        children=children,
    )


def _immediate_children(directory: str, paths: tuple[str, ...]) -> list[str]:
    prefix = "" if directory == "." else f"{directory}/"
    return [
        path
        for path in paths
        if path.startswith(prefix)
        and "/" not in path[len(prefix) :]
        and path != directory
    ]


def _child_meaning_is_unchanged(
    *,
    directory: str,
    previous_dependencies: Mapping[str, str],
    current_dependencies: Mapping[str, str],
    prior_documents: Mapping[str, SearchDocument],
    current_payload_digests: Mapping[str, str],
) -> bool:
    if previous_dependencies.keys() != current_dependencies.keys():
        return False
    prefix = "" if directory == "." else f"{directory}/"
    for name in previous_dependencies:
        path = f"{prefix}{name}"
        previous = prior_documents.get(path)
        if (
            previous is None
            or previous.payload_digest is None
            or current_payload_digests.get(path) != previous.payload_digest
        ):
            return False
    return True


def _carry_unambiguous_renames(
    prior_documents: Sequence[SearchDocument],
    *,
    current_files: Mapping[str, str],
) -> dict[str, SearchDocument]:
    """Move one known card only when old and new blob mappings are unique."""

    result = {item.path: item for item in prior_documents}
    current_by_oid: dict[str, list[str]] = defaultdict(list)
    prior_by_oid: dict[str, list[SearchDocument]] = defaultdict(list)
    for path, oid in current_files.items():
        current_by_oid[oid].append(path)
    for item in prior_documents:
        if (
            item.node_type is NodeType.FILE
            and item.source_oid
            and item.path not in current_files
        ):
            prior_by_oid[item.source_oid].append(item)
    for oid, previous in prior_by_oid.items():
        current = current_by_oid.get(oid, [])
        if len(previous) != 1 or len(current) != 1 or current[0] in result:
            continue
        result.pop(previous[0].path, None)
        result[current[0]] = previous[0].model_copy(update={"path": current[0]})
    return result
