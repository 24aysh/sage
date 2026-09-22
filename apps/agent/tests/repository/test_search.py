"""Search output compatibility and structured anchor safety."""

import json
import subprocess
from types import SimpleNamespace

import pytest

from sage.errors import RepositoryError, CommandTimeoutError
from sage.repository.search import search_text, _structured_result


class Sandbox:
    def __init__(self, root):
        self.root, self.calls = root, []

    def exec(self, command, *, timeout_seconds):
        self.calls.append((command, timeout_seconds))
        result = subprocess.run(command, cwd=self.root, shell=True, capture_output=True,
                                text=True, timeout=timeout_seconds)
        return SimpleNamespace(stdout=result.stdout, stderr=result.stderr,
                               exit_code=result.returncode, timed_out=False)


def test_single_search_keeps_display_and_handles_unusual_paths(tmp_path):
    for name in ["a:b.py", "space file.py", "λ.py"]:
        (tmp_path / name).write_text("first\nneedle here\nlast\n")
    sandbox = Sandbox(tmp_path)
    arguments = dict(query="needle", max_output_chars=1000, timeout_seconds=2)
    old = search_text(tmp_path, sandbox, **arguments)
    new = search_text(tmp_path, sandbox, structured=True, **arguments)
    assert set(old.splitlines()) == set(new.text.splitlines())
    assert {m.path for m in new.matches} == {"a:b.py", "space file.py", "λ.py"}
    assert all(m.line == 2 and m.column == 1 for m in new.matches)
    assert len(sandbox.calls) == 2  # one subprocess per invocation, never re-search for parsing
    single = dict(arguments, path="a:b.py")
    assert search_text(tmp_path, sandbox, **single) == search_text(tmp_path, sandbox, structured=True, **single).text


def test_truncated_or_invalid_json_never_authorizes_a_read():
    event = {"type": "match", "data": {"path": {"text": "a:b.py"}, "line_number": 1,
        "lines": {"text": "hello\n"}, "submatches": [{"start": 0}]}}
    raw = json.dumps(event)
    result = _structured_result(raw + '\n{"type":', 5, 1000)
    assert result.matches == () and result.truncated
    assert "a:b.py:1:1:hello" in result.text
    assert _structured_result(raw, 5, 5).matches == ()


def test_search_safety_and_errors(tmp_path):
    sandbox = Sandbox(tmp_path)
    with pytest.raises(RepositoryError):
        search_text(tmp_path, sandbox, query="x", path="../escape", structured=True,
                    max_output_chars=1000, timeout_seconds=2)
    assert not sandbox.calls
    empty = search_text(tmp_path, sandbox, query="x", structured=True, max_output_chars=1000, timeout_seconds=2)
    assert empty.text == "[no matches]" and not empty.matches
    sandbox.exec = lambda *a, **k: SimpleNamespace(timed_out=True)
    with pytest.raises(CommandTimeoutError):
        search_text(tmp_path, sandbox, query="x", structured=True, max_output_chars=1000, timeout_seconds=2)
