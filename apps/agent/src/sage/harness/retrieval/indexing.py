"""Committed-source inventory, repository identity, and graph build lifecycle."""

from __future__ import annotations

import hashlib
import sqlite3
import subprocess
from pathlib import Path, PurePosixPath
from time import perf_counter

from sage.domain.retrieval import IndexBuildResult, IndexBuildType
from sage.errors import RetrievalBuildError
from sage.harness.retrieval.parsing import PARSER_VERSION, CodeParser, ParsedFile, detect_language, normalize_path
from sage.harness.retrieval.store import GraphStore, SCHEMA_VERSION
from sage.repository.selection import IGNORED_NAMES

_IGNORED_PARTS = IGNORED_NAMES | {".hg", ".svn", ".cache", ".sage", ".retrieval"}


class RepositoryIndex:
    """Own committed-source provenance independently of query policy."""

    def __init__(self, data_root: Path | None = None) -> None:
        self._data_root = data_root

    def resolve_index_file(
        self,
        repo_root: Path,
        index_file: Path | None = None,
    ) -> Path:
        root = self.repository_root(repo_root)
        if index_file is not None:
            return index_file.expanduser().resolve()
        repository_id = self.repository_id(root)
        safe_name = "".join(
            character if character.isalnum() or character in "-." else "-"
            for character in root.name
        ).strip("-.") or "repository"
        data_root = self._data_root or _default_data_root()
        return (
            data_root.expanduser().resolve()
            / f"{safe_name}-{repository_id[:12]}"
            / "graph.sqlite3"
        )

    def repository_id(self, repo_root: Path) -> str:
        root = self.repository_root(repo_root)
        identity = self._repository_identity(root)
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()

    def build(
        self,
        *,
        repo_root: Path,
        index_file: Path | None = None,
        full_rebuild: bool = False,
    ) -> IndexBuildResult:
        """Build, update, or confirm one graph through a single entry point."""

        started = perf_counter()
        try:
            root = self.repository_root(repo_root)
            indexed_sha = self.git(root, "rev-parse", "--verify", "HEAD").strip()
            repository_id = self.repository_id(root)
            database = self.resolve_index_file(root, index_file)
            inventory = self._inventory(root)
            parser = CodeParser()
            warnings: list[str] = []

            with GraphStore(database) as store:
                existing_id = store.get_metadata("repository_id")
                if existing_id and existing_id != repository_id:
                    raise RetrievalBuildError(
                        "The selected index file belongs to a different repository."
                    )
                stored_hashes = store.file_hashes()
                ready = store.get_metadata("build_state") == "ready"
                compatible = store.get_metadata("parser_version") == PARSER_VERSION
                prior_sha = store.get_metadata("indexed_sha")
                history_compatible = bool(prior_sha) and (
                    prior_sha == indexed_sha
                    or self._git_is_ancestor(root, str(prior_sha), indexed_sha)
                )
                is_full = (
                    full_rebuild
                    or not ready
                    or not compatible
                    or not history_compatible
                    or not stored_hashes
                )
                changed = sorted(
                    path for path, file_hash in inventory.items()
                    if is_full or stored_hashes.get(path) != file_hash
                )
                removed = sorted(set(stored_hashes) - set(inventory))
                build_type = IndexBuildType.FULL if is_full else IndexBuildType.INCREMENTAL
                parsed: list[ParsedFile] = []
                failed: list[str] = []

                if not is_full and not changed and not removed:
                    build_type = IndexBuildType.NO_CHANGE
                    store.update_provenance(indexed_sha=indexed_sha, build_type=build_type.value)
                    if prior_sha != indexed_sha:
                        warnings.append("Git SHA changed without indexed file-content changes.")
                else:
                    for relative in changed:
                        try:
                            content = self._git_bytes(root, "show", f"HEAD:{relative}")
                            item = parser.parse_bytes(
                                content,
                                relative_path=relative,
                                file_hash=inventory[relative],
                            )
                        except (OSError, UnicodeError, ValueError) as error:
                            failed.append(relative)
                            warnings.append(
                                f"Skipped {relative}: {type(error).__name__}: {str(error)[:200]}"
                            )
                            continue
                        parsed.append(item)
                        warnings.extend(item.warnings)

                    store.apply_update(
                        parsed_files=parsed,
                        removed_files=(*removed, *failed),
                        repository_id=repository_id,
                        indexed_sha=indexed_sha,
                        parser_version=PARSER_VERSION,
                        build_type=build_type.value,
                        full_rebuild=is_full,
                    )
                stats = store.stats()
                return IndexBuildResult(
                    build_type=build_type,
                    index_file=database,
                    repository_id=repository_id,
                    indexed_sha=indexed_sha,
                    schema_version=SCHEMA_VERSION,
                    files_indexed=int(stats["files"]),
                    files_parsed=len(parsed),
                    files_removed=len(set((*removed, *failed))),
                    total_nodes=int(stats["nodes"]),
                    total_edges=int(stats["edges"]),
                    total_flows=int(stats["flows"]),
                    total_communities=int(stats["communities"]),
                    languages=tuple(str(item) for item in stats["languages"]),
                    warnings=tuple(warnings[:100]),
                    duration_ms=round((perf_counter() - started) * 1_000, 2),
                )
        except RetrievalBuildError:
            raise
        except (OSError, ValueError, sqlite3.Error, subprocess.SubprocessError) as error:
            raise RetrievalBuildError(
                f"Unable to build Repository retrieval index: {type(error).__name__}: {str(error)[:300]}"
            ) from error

    def _inventory(self, root: Path) -> dict[str, str]:
        output = self._git_bytes(root, "ls-tree", "-r", "-z", "HEAD")
        inventory: dict[str, str] = {}
        for entry in output.split(b"\0"):
            if not entry or b"\t" not in entry:
                continue
            metadata, raw_path = entry.split(b"\t", 1)
            fields = metadata.split()
            if len(fields) != 3 or fields[1] != b"blob":
                continue
            relative = normalize_path(
                raw_path.decode("utf-8", errors="surrogateescape")
            )
            path = PurePosixPath(relative)
            if set(part.casefold() for part in path.parts) & _IGNORED_PARTS:
                continue
            if detect_language(relative) is None:
                continue
            inventory[relative] = fields[2].decode("ascii")
        return inventory

    def repository_root(self, requested: Path) -> Path:
        path = requested.expanduser().resolve()
        if not path.is_dir():
            raise RetrievalBuildError(f"Repository path does not exist: {path}")
        try:
            result = self.git(path, "rev-parse", "--show-toplevel").strip()
        except subprocess.SubprocessError as error:
            raise RetrievalBuildError(f"Path is not a Git repository: {path}") from error
        root = Path(result).resolve()
        if root != path:
            raise RetrievalBuildError(
                f"Requested repository {path} belongs to ancestor Git root {root}. "
                "Pass the intended Git root explicitly; initialize standalone fixtures yourself."
            )
        return root

    def _repository_identity(self, root: Path) -> str:
        origin = self._git_optional(root, "config", "--get", "remote.origin.url")
        if origin:
            candidate = Path(origin).expanduser()
            if candidate.exists():
                nested = self._git_optional(
                    candidate.resolve(), "config", "--get", "remote.origin.url"
                )
                if nested:
                    origin = nested
                else:
                    return f"path:{candidate.resolve()}"
        return f"git:{origin.strip()}" if origin else f"path:{root}"

    @staticmethod
    def git(root: Path, *arguments: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "Git command failed."
            raise subprocess.CalledProcessError(
                result.returncode,
                result.args,
                output=result.stdout,
                stderr=detail,
            )
        return result.stdout

    @staticmethod
    def _git_bytes(root: Path, *arguments: str) -> bytes:
        result = subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=False,
            capture_output=True,
            timeout=60,
        )
        if result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, result.args)
        return result.stdout

    def _git_optional(self, root: Path, *arguments: str) -> str:
        try:
            return self.git(root, *arguments).strip()
        except (OSError, subprocess.SubprocessError):
            return ""

    @staticmethod
    def _git_is_ancestor(root: Path, older: str, newer: str) -> bool:
        result = subprocess.run(
            ["git", "-C", str(root), "merge-base", "--is-ancestor", older, newer],
            check=False,
            capture_output=True,
            timeout=60,
        )
        return result.returncode == 0


def _default_data_root() -> Path:
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "apps" / "agent" / "pyproject.toml").is_file():
            return candidate / ".sage" / "retrieval"
    return Path.home() / ".local" / "state" / "sage" / "retrieval"
