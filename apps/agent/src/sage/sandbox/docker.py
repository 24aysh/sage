"""Docker CLI implementation of the repository sandbox."""

from __future__ import annotations

import logging
import subprocess

from sage.config import Settings
from sage.domain.solve import PreparedRun
from sage.errors import SandboxError
from sage.sandbox.base import CommandResult

logger = logging.getLogger(__name__)


class DockerSandbox:
    """A disposable, network-disabled container with one workspace mount."""

    def __init__(self, *, prepared_run: PreparedRun, settings: Settings) -> None:
        self._workspace_dir = prepared_run.workspace_dir.resolve()
        workspace_stat = self._workspace_dir.stat()
        self._workspace_owner = f"{workspace_stat.st_uid}:{workspace_stat.st_gid}"
        self._image = settings.sandbox_image
        self._default_timeout = settings.command_timeout_seconds
        self._container_name = f"sage-{prepared_run.run_id}"
        self._running = False

    @property
    def container_name(self) -> str:
        return self._container_name

    def start(self) -> None:
        if self._running:
            raise SandboxError("Docker sandbox is already running.")

        command = [
            "docker",
            "run",
            "--detach",
            "--rm",
            "--name",
            self._container_name,
            "--user",
            self._workspace_owner,
            "--network",
            "none",
            "--cpus",
            "2",
            "--memory",
            "4g",
            "--pids-limit",
            "256",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--env",
            "HOME=/tmp",
            "--env",
            "LANG=C.UTF-8",
            "--env",
            "LC_ALL=C.UTF-8",
            "--mount",
            f"type=bind,src={self._workspace_dir},dst=/workspace",
            "--workdir",
            "/workspace",
            self._image,
            "sleep",
            "infinity",
        ]
        completed = _run_docker(command, timeout_seconds=30)
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise SandboxError(f"Unable to start Docker sandbox: {detail}")

        self._running = True
        logger.info("sandbox started", extra={"container": self._container_name})

    def exec(
        self,
        command: str,
        *,
        timeout_seconds: int | None = None,
    ) -> CommandResult:
        if not self._running:
            raise SandboxError("Docker sandbox is not running.")

        timeout = timeout_seconds or self._default_timeout
        docker_command = [
            "docker",
            "exec",
            "--workdir",
            "/workspace",
            self._container_name,
            "timeout",
            "--signal=TERM",
            "--kill-after=2",
            f"{timeout}s",
            "bash",
            "-lc",
            command,
        ]
        try:
            completed = _run_docker(docker_command, timeout_seconds=timeout + 5)
        except subprocess.TimeoutExpired as error:
            stdout = _timeout_output(error.stdout)
            stderr = _timeout_output(error.stderr)
            logger.warning(
                "sandbox command timed out",
                extra={"container": self._container_name, "timeout_seconds": timeout},
            )
            return CommandResult(
                command=command,
                exit_code=124,
                stdout=stdout,
                stderr=stderr,
                timed_out=True,
            )

        timed_out = completed.returncode in {124, 137}
        logger.info(
            "sandbox command completed",
            extra={
                "container": self._container_name,
                "exit_code": completed.returncode,
                "timed_out": timed_out,
            },
        )
        return CommandResult(
            command=command,
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            timed_out=timed_out,
        )

    def stop(self) -> None:
        if not self._running:
            return

        self._running = False
        try:
            completed = _run_docker(
                ["docker", "rm", "--force", self._container_name],
                timeout_seconds=30,
            )
        except SandboxError as error:
            logger.warning(
                "sandbox cleanup failed",
                extra={"container": self._container_name, "error": str(error)},
            )
            return
        if completed.returncode != 0:
            logger.warning(
                "sandbox cleanup failed",
                extra={
                    "container": self._container_name,
                    "stderr": completed.stderr.strip(),
                },
            )
            return
        logger.info("sandbox stopped", extra={"container": self._container_name})


def _run_docker(
    command: list[str], *, timeout_seconds: int
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except FileNotFoundError as error:
        raise SandboxError("Docker executable was not found.") from error


def _timeout_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
