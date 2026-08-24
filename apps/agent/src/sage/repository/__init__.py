"""Provider-neutral repository tool façade."""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from time import perf_counter
from typing import Iterator

from sage.config import Settings
from sage.repository.commands import run_command as execute_command
from sage.repository.edits import (
    WriteMode,
    delete_file as delete_repository_file,
    move_file as move_repository_file,
    replace_text as replace_repository_text,
    write_file as write_repository_file,
)
from sage.repository.files import read_file as read_repository_file
from sage.repository.git import (
    get_changed_files as read_changed_files,
    get_complete_diff as read_complete_diff,
    get_head_sha as read_head_sha,
    show_diff as render_diff,
)
from sage.repository.output import truncate_text
from sage.repository.patch import apply_patch as apply_repository_patch
from sage.repository.search import search_text as search_repository_text
from sage.repository.tree import list_tree as render_tree
from sage.sandbox.base import CommandResult, Sandbox

logger = logging.getLogger(__name__)


class RepositoryTools:
    """Deterministic operations exposed to an agent runtime."""

    def __init__(
        self,
        *,
        workspace_root: Path,
        sandbox: Sandbox,
        settings: Settings,
    ) -> None:
        self._workspace_root = workspace_root
        self._sandbox = sandbox
        self._settings = settings

    def list_tree(self, *, path: str = ".", max_depth: int = 2) -> str:
        with _tool_call("list_tree"):
            return truncate_text(
                render_tree(self._workspace_root, path=path, max_depth=max_depth),
                self._settings.max_tool_output_chars,
            )

    def search_text(
        self,
        *,
        query: str,
        path: str = ".",
        max_results: int = 50,
    ) -> str:
        with _tool_call("search_text"):
            return search_repository_text(
                self._workspace_root,
                self._sandbox,
                query=query,
                path=path,
                max_results=max_results,
                max_output_chars=self._settings.max_tool_output_chars,
                timeout_seconds=self._settings.command_timeout_seconds,
            )

    def read_file(
        self,
        *,
        path: str,
        start_line: int = 1,
        end_line: int | None = None,
    ) -> str:
        with _tool_call("read_file"):
            return read_repository_file(
                self._workspace_root,
                path=path,
                start_line=start_line,
                end_line=end_line,
                max_output_chars=self._settings.max_tool_output_chars,
            )

    def apply_patch(self, *, patch: str) -> str:
        with _tool_call("apply_patch"):
            return apply_repository_patch(
                self._workspace_root,
                self._sandbox,
                patch=patch,
                max_output_chars=self._settings.max_tool_output_chars,
                timeout_seconds=self._settings.command_timeout_seconds,
            )

    def replace_text(
        self,
        *,
        path: str,
        old_text: str,
        new_text: str,
        expected_occurrences: int = 1,
    ) -> str:
        with _tool_call("replace_text"):
            return replace_repository_text(
                self._workspace_root,
                path=path,
                old_text=old_text,
                new_text=new_text,
                expected_occurrences=expected_occurrences,
            )

    def write_file(self, *, path: str, content: str, mode: WriteMode) -> str:
        with _tool_call("write_file"):
            return write_repository_file(
                self._workspace_root,
                path=path,
                content=content,
                mode=mode,
            )

    def delete_file(self, *, path: str) -> str:
        with _tool_call("delete_file"):
            return delete_repository_file(self._workspace_root, path=path)

    def move_file(self, *, source_path: str, destination_path: str) -> str:
        with _tool_call("move_file"):
            return move_repository_file(
                self._workspace_root,
                source_path=source_path,
                destination_path=destination_path,
            )

    def show_diff(self) -> str:
        with _tool_call("show_diff"):
            return render_diff(
                self._sandbox,
                max_output_chars=self._settings.max_tool_output_chars,
                timeout_seconds=self._settings.command_timeout_seconds,
            )

    def run_command(
        self,
        *,
        command: str,
        timeout_seconds: int | None = None,
    ) -> CommandResult:
        with _tool_call("run_command"):
            return execute_command(
                self._sandbox,
                command=command,
                timeout_seconds=timeout_seconds,
                default_timeout_seconds=self._settings.command_timeout_seconds,
                max_output_chars=self._settings.max_tool_output_chars,
            )

    def get_complete_diff(self) -> str:
        with _tool_call("get_complete_diff"):
            return read_complete_diff(
                self._sandbox,
                timeout_seconds=self._settings.command_timeout_seconds,
            )

    def get_changed_files(self) -> list[str]:
        with _tool_call("get_changed_files"):
            return read_changed_files(
                self._sandbox,
                timeout_seconds=self._settings.command_timeout_seconds,
            )

    def get_head_sha(self) -> str:
        with _tool_call("get_head_sha"):
            return read_head_sha(
                self._sandbox,
                timeout_seconds=self._settings.command_timeout_seconds,
            )

    def format_command_result(self, result: CommandResult) -> str:
        """Serialize a command result as valid JSON within the tool output cap."""

        payload = asdict(result)
        for _ in range(20):
            rendered = json.dumps(payload, ensure_ascii=False)
            if len(rendered) <= self._settings.max_tool_output_chars:
                return rendered

            string_fields = ("stdout", "stderr", "command")
            largest = max(string_fields, key=lambda key: len(str(payload[key])))
            value = str(payload[largest])
            excess = len(rendered) - self._settings.max_tool_output_chars
            target_size = max(0, len(value) - excess - 16)
            payload[largest] = truncate_text(value, target_size) if target_size else ""

        # Metadata alone is always small relative to the configured minimum.
        payload.update({"command": "", "stdout": "", "stderr": ""})
        return json.dumps(payload, ensure_ascii=False)


@contextmanager
def _tool_call(tool_name: str) -> Iterator[None]:
    started = perf_counter()
    try:
        yield
    finally:
        logger.info(
            "repository tool completed",
            extra={
                "tool_name": tool_name,
                "duration_ms": round((perf_counter() - started) * 1000, 2),
            },
        )


__all__ = ["RepositoryTools"]
