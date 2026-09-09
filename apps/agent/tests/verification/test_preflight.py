from types import SimpleNamespace
import shlex

import pytest

from sage.config import Settings, ConfiguredVerificationCommand
from sage.errors import WorkspaceError
from sage.sandbox.base import CommandResult
from sage.verification.preflight import verification_environment_preflight


@pytest.mark.parametrize("command", ["pytest -q", "python -m pytest", "python3 -m pytest -q"])
def test_preflight_only_probes_tooling(command):
    calls = []
    def execute(probe, **kwargs):
        calls.append(probe)
        assert " -I -B -c " in probe
        assert "m.version('pymongo')" in shlex.split(probe)[-1]
        assert "pip install" not in probe
        return CommandResult(probe, 0, "", "")
    settings = Settings(openai_api_key="test", verification_commands=(ConfiguredVerificationCommand(id="tests", command=command),),
                        verification_preflight_dependencies=("pymongo",))
    result = verification_environment_preflight(SimpleNamespace(exec=execute), settings)
    assert result["status"] == "ready"
    assert len(calls) == 1


def test_missing_environment_fails_before_solving():
    settings = Settings(openai_api_key="test", verification_commands=(ConfiguredVerificationCommand(id="tests", command="python -m pytest"),))
    sandbox = SimpleNamespace(exec=lambda command, **kwargs: CommandResult(command, 127, "", "missing python"))
    with pytest.raises(WorkspaceError, match="before model calls"):
        verification_environment_preflight(sandbox, settings)


@pytest.mark.parametrize("commands", [(), (ConfiguredVerificationCommand(id="tests", command="npm test"),)])
def test_missing_or_unsupported_command_is_not_silently_ready(commands):
    with pytest.raises(WorkspaceError):
        verification_environment_preflight(SimpleNamespace(), Settings(openai_api_key="test", verification_commands=commands))
