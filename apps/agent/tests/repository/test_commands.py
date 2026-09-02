import json
from pathlib import Path

from sage.config import Settings
from sage.repository.service import Repository
from sage.repository.commands import run_command
from sage.sandbox.base import CommandResult


class RecordingSandbox:
    def __init__(self, result: CommandResult) -> None:
        self.result = result
        self.timeout_seconds: int | None = None

    def start(self) -> None:
        pass

    def exec(
        self,
        command: str,
        *,
        timeout_seconds: int | None = None,
    ) -> CommandResult:
        self.timeout_seconds = timeout_seconds
        return self.result

    def stop(self) -> None:
        pass


def test_run_command_caps_timeout_and_output() -> None:
    sandbox = RecordingSandbox(
        CommandResult(
            command="test",
            exit_code=0,
            stdout="a" * 2_000,
            stderr="b" * 2_000,
        )
    )

    result = run_command(
        sandbox,
        command="test",
        timeout_seconds=500,
        default_timeout_seconds=30,
        max_output_chars=1_000,
    )

    assert sandbox.timeout_seconds == 30
    assert len(result.stdout) + len(result.stderr) <= 1_000
    assert "truncated" in result.stdout
    assert "truncated" in result.stderr


def test_repository_formats_command_result_as_bounded_valid_json(
    tmp_path: Path,
) -> None:
    raw_result = CommandResult(
        command="x" * 4_000,
        exit_code=1,
        stdout='"\\\n' * 2_000,
        stderr="error" * 1_000,
    )
    repository = Repository(
        workspace_root=tmp_path,
        sandbox=RecordingSandbox(raw_result),
        settings=Settings(openai_api_key="test", max_tool_output_chars=1_000),
    )

    rendered = repository.format_command_result(raw_result)

    assert len(rendered) <= 1_000
    assert json.loads(rendered)["exit_code"] == 1
