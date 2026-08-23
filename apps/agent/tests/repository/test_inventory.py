from __future__ import annotations

import json
import subprocess
from pathlib import Path

from sage.repository.inventory import search_literal_matches, tracked_inventory
from sage.sandbox.base import CommandResult


class RecordingSandbox:
    def __init__(self, result: CommandResult) -> None:
        self.result = result
        self.commands: list[str] = []

    def exec(self, command: str, *, timeout_seconds: int | None = None) -> CommandResult:
        del timeout_seconds
        self.commands.append(command)
        return self.result


def test_tracked_inventory_is_stable_and_excludes_untracked_noise(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True)
    tracked = tmp_path / "src" / "spaced ünicode.py"
    tracked.parent.mkdir()
    tracked.write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "untracked.txt").write_text("noise\n", encoding="utf-8")
    subprocess.run(["git", "add", "src/spaced ünicode.py"], cwd=tmp_path, check=True)

    inventory = tracked_inventory(tmp_path)

    assert inventory.paths == ("src/spaced ünicode.py",)
    assert inventory.total_count == 1
    assert inventory.truncated is False


def test_literal_search_quotes_repository_input_and_parses_json() -> None:
    event = {
        "type": "match",
        "data": {
            "path": {"text": "./src/app.py"},
            "lines": {"text": "dangerous; value\n"},
            "line_number": 7,
        },
    }
    sandbox = RecordingSandbox(
        CommandResult(
            command="rg",
            exit_code=0,
            stdout=json.dumps(event),
            stderr="",
        )
    )

    matches = search_literal_matches(
        sandbox,
        query="danger'; touch /tmp/never",
        max_results=2,
        timeout_seconds=10,
    )

    assert matches[0].path == "src/app.py"
    assert matches[0].line == 7
    assert "-e 'danger'\"'\"'; touch /tmp/never'" in sandbox.commands[0]
