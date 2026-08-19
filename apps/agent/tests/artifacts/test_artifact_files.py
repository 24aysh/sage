import json
from pathlib import Path

from sage.artifacts.files import write_json_atomic, write_text_atomic


def test_write_json_atomic_is_deterministic(tmp_path: Path) -> None:
    destination = tmp_path / "value.json"

    write_json_atomic(destination, {"z": 1, "a": [2]})

    assert destination.read_text(encoding="utf-8") == (
        '{\n  "a": [\n    2\n  ],\n  "z": 1\n}\n'
    )
    assert json.loads(destination.read_text(encoding="utf-8")) == {
        "a": [2],
        "z": 1,
    }


def test_write_text_atomic_replaces_existing_file_without_temp_file(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "value.txt"
    destination.write_text("old", encoding="utf-8")

    write_text_atomic(destination, "new")

    assert destination.read_text(encoding="utf-8") == "new"
    assert [path.name for path in tmp_path.iterdir()] == ["value.txt"]
