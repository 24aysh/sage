"""Strict dataset loading and repository-relative path identity."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath

from evals.retrieval.models import Dataset, DatasetIssue


class DatasetError(ValueError):
    """The requested evaluation dataset is invalid."""


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise DatasetError(f"Duplicate JSON object key: {key}")
        result[key] = value
    return result


def normalize_repo_path(value: str) -> str:
    """Return a strict case-sensitive, repository-relative POSIX identity."""

    if not isinstance(value, str) or not value:
        raise DatasetError("Correct-file paths must be nonempty strings.")
    if "\\" in value or re.match(r"^[A-Za-z]:", value) or value.startswith("//"):
        raise DatasetError(f"Correct-file path is not relative POSIX syntax: {value!r}")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise DatasetError("Correct-file paths cannot contain control characters.")
    if any(character in value for character in "*?[]"):
        raise DatasetError(f"Correct-file paths cannot contain globs: {value!r}")
    raw_parts = value.split("/")
    if ".." in raw_parts:
        raise DatasetError(f"Correct-file paths cannot traverse parents: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute():
        raise DatasetError(f"Correct-file paths must be repository-relative: {value!r}")
    normalized = path.as_posix()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    normalized = PurePosixPath(normalized).as_posix()
    if normalized in {"", "."} or normalized.startswith("../"):
        raise DatasetError(f"Invalid correct-file path: {value!r}")
    return normalized


def load_dataset(issues_dir: Path, issue_count: int) -> Dataset:
    """Validate and load every selected Issue before any provider call."""

    if issue_count < 1:
        raise DatasetError("ISSUE_COUNT must be a positive integer.")
    directory = issues_dir.expanduser().resolve()
    if not directory.is_dir():
        raise DatasetError(f"Issue directory does not exist: {directory}")
    correct_file = directory / "correct.json"
    try:
        correct_bytes = correct_file.read_bytes()
    except OSError as error:
        raise DatasetError(f"Unable to read {correct_file}: {error}") from error
    try:
        labels = json.loads(correct_bytes.decode("utf-8"), object_pairs_hook=_unique_object)
    except UnicodeDecodeError as error:
        raise DatasetError("correct.json must be UTF-8.") from error
    except json.JSONDecodeError as error:
        raise DatasetError(f"correct.json is invalid JSON: {error}") from error
    if not isinstance(labels, dict):
        raise DatasetError("correct.json must contain one object keyed by issue_N.")

    issues: list[DatasetIssue] = []
    for number in range(1, issue_count + 1):
        issue_id = f"issue_{number}"
        issue_path = directory / f"issue-{number}.md"
        try:
            issue_bytes = issue_path.read_bytes()
            issue_text = issue_bytes.decode("utf-8")
        except OSError as error:
            raise DatasetError(f"Unable to read {issue_path}: {error}") from error
        except UnicodeDecodeError as error:
            raise DatasetError(f"{issue_path.name} must be UTF-8.") from error
        if not issue_text.strip():
            raise DatasetError(f"{issue_path.name} cannot be empty.")
        if issue_id not in labels:
            raise DatasetError(f"correct.json is missing {issue_id}.")
        raw_paths = labels[issue_id]
        if not isinstance(raw_paths, list) or not raw_paths:
            raise DatasetError(f"{issue_id} must be a nonempty JSON array of paths.")
        normalized: list[str] = []
        for value in raw_paths:
            if not isinstance(value, str):
                raise DatasetError(f"{issue_id} contains a non-string path.")
            normalized.append(normalize_repo_path(value))
        unique = tuple(dict.fromkeys(normalized))
        issues.append(DatasetIssue(
            number=number,
            issue_id=issue_id,
            path=issue_path,
            text=issue_text,
            sha256=hashlib.sha256(issue_bytes).hexdigest(),
            gold_files=unique,
            duplicate_gold_paths=len(normalized) - len(unique),
        ))
    return Dataset(
        issues_dir=directory,
        correct_file=correct_file,
        correct_sha256=hashlib.sha256(correct_bytes).hexdigest(),
        issues=tuple(issues),
    )
