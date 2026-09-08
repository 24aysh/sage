"""Read-only tooling probes before model calls, shared by local solve modes."""

from __future__ import annotations

import json
import shlex

from sage.config import Settings
from sage.errors import WorkspaceError
from sage.sandbox.base import Sandbox


def verification_environment_preflight(sandbox: Sandbox, settings: Settings) -> dict[str, object]:
    """Check trusted Python test commands and installed distributions, not tests.

    No installation, networking, repository imports or test execution is allowed.
    This is tooling readiness, not a verdict on the broken base or a patch.
    """
    commands = [item.command for item in settings.verification_commands if item.required]
    if not commands:
        raise WorkspaceError("Verification preflight needs a required command in SAGE_VERIFICATION_COMMANDS_JSON.")
    probes = []
    for command in commands:
        parts = shlex.split(command)
        if parts[:1] == ["pytest"]:
            executable = "python3"
            prefix = "command -v pytest >/dev/null && "
        elif len(parts) >= 3 and parts[0] in {"python", "python3"} and parts[1:3] == ["-m", "pytest"]:
            executable, prefix = parts[0], ""
        else:
            raise WorkspaceError("Verification preflight currently supports pytest or python[3] -m pytest; configure a supported required command.")
        distributions = ["pytest", *settings.verification_preflight_dependencies]
        script = "import importlib.metadata as m; " + "; ".join(
            f"m.version({distribution!r})" for distribution in distributions
        )
        # -I excludes repository/PYTHONPATH imports; -B prevents bytecode writes.
        probe = prefix + executable + " -I -B -c " + shlex.quote(script)
        result = sandbox.exec(probe, timeout_seconds=min(30, settings.command_timeout_seconds))
        if result.exit_code or result.timed_out:
            raise WorkspaceError(
                "Verification environment unavailable before model calls: "
                f"{command}. Check the sandbox interpreter and installed distributions "
                f"{json.dumps(distributions)}; use a prepared image. No dependencies were installed."
            )
        probes.append({"command": command, "probe": probe, "exit_code": result.exit_code})
    return {"status": "ready", "scope": "tooling only; tests not executed", "probes": probes}
