import json
from pathlib import Path

import pytest

from evals.retrieval.dataset import DatasetError, load_dataset, normalize_repo_path


def _dataset(tmp_path: Path, labels: object) -> Path:
    directory = tmp_path / "issues"
    directory.mkdir()
    (directory / "issue-1.md").write_text("Fix the helper", encoding="utf-8")
    (directory / "correct.json").write_text(json.dumps(labels), encoding="utf-8")
    return directory


def test_dataset_normalizes_and_deduplicates_paths(tmp_path: Path) -> None:
    directory = _dataset(
        tmp_path,
        {"issue_1": ["./src//main.py", "src/main.py", "tests/test_main.py"]},
    )

    dataset = load_dataset(directory, 1)

    assert dataset.issues[0].gold_files == ("src/main.py", "tests/test_main.py")
    assert dataset.issues[0].duplicate_gold_paths == 1
    assert dataset.issues[0].text == "Fix the helper"


@pytest.mark.parametrize(
    "value",
    ["/tmp/main.py", "C:/main.py", "../main.py", "src\\main.py", "src/*.py", "src/\x00.py"],
)
def test_repository_paths_reject_ambiguous_or_unsafe_identity(value: str) -> None:
    with pytest.raises(DatasetError):
        normalize_repo_path(value)


def test_dataset_requires_every_numbered_issue_and_key(tmp_path: Path) -> None:
    directory = _dataset(tmp_path, {"issue_1": ["main.py"]})

    with pytest.raises(DatasetError, match="issue-2.md"):
        load_dataset(directory, 2)


def test_dataset_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    directory = tmp_path / "issues"
    directory.mkdir()
    (directory / "issue-1.md").write_text("Fix it", encoding="utf-8")
    (directory / "correct.json").write_text(
        '{"issue_1":["a.py"],"issue_1":["b.py"]}', encoding="utf-8"
    )

    with pytest.raises(DatasetError, match="Duplicate JSON object key"):
        load_dataset(directory, 1)


@pytest.mark.parametrize("labels", [{"issue_1": []}, {"issue_1": "main.py"}, {}])
def test_dataset_rejects_missing_or_non_array_gold(labels: object, tmp_path: Path) -> None:
    with pytest.raises(DatasetError):
        load_dataset(_dataset(tmp_path, labels), 1)
