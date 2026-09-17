import subprocess
from pathlib import Path

from sage.config import Settings
from sage.domain.solve import PreparedRun
from sage.sandbox.docker import DockerSandbox


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]


def test_canonical_image_provides_python_and_node_test_runtimes() -> None:
    dockerfile = (REPOSITORY_ROOT / "docker/sandbox/Dockerfile").read_text(
        encoding="utf-8"
    )
    makefile = (REPOSITORY_ROOT / "Makefile").read_text(encoding="utf-8")
    smoke_target = makefile.split("sandbox-smoke:", 1)[1].split("\ntest:", 1)[0]

    assert "FROM node:24-bookworm-slim AS node-runtime" in dockerfile
    assert "FROM python:3.14-slim-bookworm" in dockerfile
    assert "COPY --from=node-runtime /usr/local/bin/node" in dockerfile
    assert "COPY --from=node-runtime /usr/local/lib/node_modules/npm" in dockerfile
    assert "python3 -m pip install --no-cache-dir pytest==9.1.1" in dockerfile
    for probe in (
        "pytest --version",
        "python3 -m pytest --version",
        "node --version",
        "npm --version",
        'require(\\"node:test\\")',
    ):
        assert probe in smoke_target


def test_docker_sandbox_starts_with_only_isolated_workspace(
    tmp_path: Path,
    monkeypatch,
) -> None:
    commands: list[list[str]] = []

    def fake_run(command: list[str], *, timeout_seconds: int):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="container-id\n", stderr="")

    monkeypatch.setattr("sage.sandbox.docker._run_docker", fake_run)
    workspace = tmp_path / "repo"
    workspace.mkdir()
    prepared = _prepared_run(tmp_path, workspace)
    settings = Settings(openai_api_key="super-secret")
    sandbox = DockerSandbox(prepared_run=prepared, settings=settings)

    sandbox.start()
    sandbox.stop()

    start_command = commands[0]
    rendered = " ".join(start_command)
    expected_user = f"{workspace.stat().st_uid}:{workspace.stat().st_gid}"
    assert sandbox.container_name == f"sage-{prepared.run_id}"
    assert start_command.count("--mount") == 1
    assert f"src={workspace.resolve()},dst=/workspace" in rendered
    assert f"--user {expected_user}" in rendered
    assert "--network none" in rendered
    assert "--cap-drop ALL" in rendered
    assert "super-secret" not in rendered
    assert "/var/run/docker.sock" not in rendered


def test_docker_sandbox_returns_timeout_result(tmp_path: Path, monkeypatch) -> None:
    def fake_run(command: list[str], *, timeout_seconds: int):
        if command[1] == "run":
            return subprocess.CompletedProcess(command, 0, stdout="id\n", stderr="")
        raise subprocess.TimeoutExpired(command, timeout_seconds, output="partial")

    monkeypatch.setattr("sage.sandbox.docker._run_docker", fake_run)
    workspace = tmp_path / "repo"
    workspace.mkdir()
    sandbox = DockerSandbox(
        prepared_run=_prepared_run(tmp_path, workspace),
        settings=Settings(openai_api_key="test"),
    )
    sandbox.start()

    result = sandbox.exec("slow command", timeout_seconds=1)

    assert result.timed_out is True
    assert result.exit_code == 124
    assert result.stdout == "partial"


def test_docker_sandbox_enforces_timeout_inside_container(
    tmp_path: Path,
    monkeypatch,
) -> None:
    commands: list[list[str]] = []

    def fake_run(command: list[str], *, timeout_seconds: int):
        commands.append(command)
        exit_code = 0 if command[1] == "run" else 124
        return subprocess.CompletedProcess(command, exit_code, stdout="", stderr="")

    monkeypatch.setattr("sage.sandbox.docker._run_docker", fake_run)
    workspace = tmp_path / "repo"
    workspace.mkdir()
    sandbox = DockerSandbox(
        prepared_run=_prepared_run(tmp_path, workspace),
        settings=Settings(openai_api_key="test"),
    )
    sandbox.start()

    result = sandbox.exec("slow command", timeout_seconds=3)

    exec_command = commands[1]
    assert exec_command[exec_command.index("timeout") : exec_command.index("bash")] == [
        "timeout",
        "--signal=TERM",
        "--kill-after=2",
        "3s",
    ]
    assert result.timed_out is True


def _prepared_run(tmp_path: Path, workspace: Path) -> PreparedRun:
    return PreparedRun(
        run_id="20260816T000000Z-12345678",
        source_repo=tmp_path / "source",
        run_dir=tmp_path,
        workspace_dir=workspace,
        base_ref="HEAD",
        base_sha="a" * 40,
    )
