from pathlib import Path
import os
import subprocess
import sys
import textwrap

import pytest

from sage.cli import memory as cli_memory, solve
from sage.cli.app import _build_parser
from sage.config import Settings
from sage.domain.memory import LegionMemoryRunArtifact, MemoryRetrievalStatus
from sage.domain.solve import SolveOutcome, SolveResult
from sage.domain.usage import AgentToolCallRecord, AttemptKind, ModelCallRecord, ModelRole, RunProvenance


def test_local_solve_arguments_remain_compatible(tmp_path: Path) -> None:
    arguments = _build_parser().parse_args(
        [
            "solve",
            "--repo",
            str(tmp_path / "repo"),
            "--issue-file",
            str(tmp_path / "issue.md"),
            "--base-ref",
            "main",
            "--sandbox-image",
            "custom:test",
            "--debug",
        ]
    )

    assert arguments.command == "solve"
    assert arguments.repo == tmp_path / "repo"
    assert arguments.issue_file == tmp_path / "issue.md"
    assert arguments.base_ref == "main"
    assert arguments.sandbox_image == "custom:test"
    assert arguments.memory_file is None
    assert arguments.debug is True


def test_local_solve_accepts_explicit_memory_file(tmp_path: Path) -> None:
    memory_file = tmp_path / "graph.sqlite3"
    arguments = _build_parser().parse_args(
        [
            "solve",
            "--repo",
            str(tmp_path / "repo"),
            "--issue-file",
            str(tmp_path / "issue.md"),
            "--memory-file",
            str(memory_file),
        ]
    )

    assert arguments.memory_file == memory_file


def test_non_publishable_partial_diff_returns_exit_two(
    monkeypatch,
    tmp_path: Path,
) -> None:
    settings = Settings(
        openai_api_key="openai-test",
        gemini_api_key="gemini-test",
        google_model_context_approved=True,
    )
    result = SolveResult(
        run_id="run-id",
        base_sha="a" * 40,
        summary="Verification failed.",
        remaining_uncertainty=[],
        changed_files=["app.py"],
        diff="diff --git a/app.py b/app.py\n",
        run_dir=tmp_path,
        workspace_dir=tmp_path / "repo",
        outcome=SolveOutcome.VERIFICATION_FAILED,
    )
    monkeypatch.setattr(solve.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(solve, "_validate_prerequisites", lambda *args, **kwargs: None)
    monkeypatch.setattr(solve, "build_orchestrator", lambda value: object())

    async def fake_solve(request, orchestrator, effective_settings, *, on_interrupted):
        return result

    monkeypatch.setattr(solve, "solve_issue", fake_solve)
    arguments = _build_parser().parse_args(
        [
            "solve",
            "--repo",
            str(tmp_path / "repo"),
            "--issue-file",
            str(tmp_path / "issue.md"),
        ]
    )

    assert solve._run_local_solve(arguments) == 2


def test_memory_solve_injects_service_and_reports_comparable_usage(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    settings = Settings(
        openai_api_key="openai-test",
        gemini_api_key="gemini-test",
        google_model_context_approved=True,
    )
    memory_file = tmp_path / "graph.sqlite3"
    service = object()
    memory = LegionMemoryRunArtifact(
        requested_memory_file=memory_file,
        resolved_memory_file=memory_file,
        status=MemoryRetrievalStatus.NO_MATCH,
        fallback="normal repository inspection",
    )
    provenance = RunProvenance(
        calls=(
            ModelCallRecord(
                call_number=1,
                stage="solver",
                role=ModelRole.SOLVER,
                attempt_kind=AttemptKind.PRIMARY,
                provider="openai",
                model="test-model",
                input_tokens=20,
                output_tokens=5,
                cached_tokens=4,
                latency_ms=1,
                outcome="success",
            ),
        ),
        tool_calls=(
            AgentToolCallRecord(
                call_number=1,
                model_call_number=1,
                stage="solver",
                role=ModelRole.SOLVER,
                tool_name="read_file",
            ),
            AgentToolCallRecord(
                call_number=2,
                model_call_number=1,
                stage="solver",
                role=ModelRole.SOLVER,
                tool_name="read_file",
            ),
        ),
        commands=("pytest -q", "git diff --check HEAD --"),
    )
    result = SolveResult(
        run_id="run-id",
        base_sha="a" * 40,
        summary="No change required.",
        remaining_uncertainty=[],
        changed_files=[],
        diff="",
        run_dir=tmp_path,
        workspace_dir=tmp_path / "repo",
        outcome=SolveOutcome.NO_CHANGE,
        provenance=provenance,
        memory=memory,
    )
    monkeypatch.setattr(solve.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(solve, "_validate_prerequisites", lambda *args, **kwargs: None)
    monkeypatch.setattr(solve, "build_orchestrator", lambda value: object())
    monkeypatch.setattr(solve, "build_legion_memory_service", lambda: service)

    async def fake_solve(
        request,
        orchestrator,
        effective_settings,
        *,
        memory_service,
        on_interrupted,
    ):
        assert request.memory_file == memory_file
        assert memory_service is service
        return result

    monkeypatch.setattr(solve, "solve_issue", fake_solve)
    arguments = _build_parser().parse_args(
        [
            "solve",
            "--repo",
            str(tmp_path / "repo"),
            "--issue-file",
            str(tmp_path / "issue.md"),
            "--memory-file",
            str(memory_file),
        ]
    )

    assert solve._run_local_solve(arguments) == 2
    output = capsys.readouterr().out
    assert "Legion Memory:" in output
    assert "Status: no_match" in output
    assert "Total tool calls: 2" in output
    assert "Tools: read_file=2" in output
    assert 'Commands: ["pytest -q", "git diff --check HEAD --"]' in output
    assert "Total tokens: 25" in output


@pytest.mark.parametrize("with_memory", [False, True])
def test_sigint_prints_partial_run_summary_and_preserves_cleanup(tmp_path, with_memory):
    script = textwrap.dedent('''
        import asyncio, signal, sys
        from pathlib import Path
        from langchain_core.messages import AIMessage
        from sage.cli import solve as cli
        from sage.cli.app import main
        from sage.config import Settings
        from sage.domain.solve import PreparedRun
        from sage.domain.usage import ModelRole
        from sage.providers.calls import ModelCalls
        from sage.workflows import solve as workflow

        root = Path(sys.argv[1])
        issue = root / "issue.md"
        issue.write_text("Fix it.")
        settings = Settings(openai_api_key="test", gemini_api_key="test")
        prepared = PreparedRun(run_id="sigint-run", source_repo=root, run_dir=root / "run",
            workspace_dir=root, base_sha="a" * 40, base_ref="HEAD")
        prepared.run_dir.mkdir()
        workflow.prepare_run = lambda *args: prepared

        class Sandbox:
            def start(self): pass
            def stop(self): print("sandbox cleanup completed")

        class Provider:
            provider_name = "google"
            model_name = "reviewer"
            async def invoke_structured(self, **kwargs):
                asyncio.get_running_loop().call_soon(signal.raise_signal, signal.SIGINT)
                await asyncio.Future()

        class Engine:
            async def solve(self, *, context, **kwargs):
                calls = ModelCalls(settings=settings, reviewer=Provider(), usage_writer=context.artifacts.write_usage)
                number = calls.start_coding_call(role=ModelRole.SOLVER, stage="solver")
                calls.finish_coding_call(role=ModelRole.SOLVER, stage="solver", call_number=number,
                    latency_ms=10, message=AIMessage(content="done", usage_metadata={
                        "input_tokens": 20, "output_tokens": 5, "total_tokens": 25}))
                await calls.measure_agent(role="reviewer", stage="review", operation=calls.invoke_reviewer(
                    stage="review", messages=[], schema=PreparedRun))

        cli.Settings.from_env = lambda: settings
        cli._validate_prerequisites = lambda *args, **kwargs: None
        cli.build_orchestrator = lambda settings: Engine()
        cli._memory_service = lambda arguments: None
        cli.solve_issue = lambda *args, **kwargs: workflow.solve_issue(*args, **kwargs,
            sandbox_factory=lambda *args: Sandbox(), repository_factory=lambda *args: object())
        arguments = ["solve", "--repo", str(root), "--issue-file", str(issue)]
        if sys.argv[2] == "memory":
            arguments += ["--memory-file", str(root / "graph.sqlite3")]
        sys.exit(main(arguments))
    ''')
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path), "memory" if with_memory else "plain"],
        capture_output=True, text=True, timeout=20, env={**os.environ, "LANGSMITH_TRACING": "false"})
    assert result.returncode == 1, result.stderr
    assert "Solve interrupted" in result.stdout
    assert "Total tokens: 25" in result.stdout
    assert "Token usage incomplete: 1 call(s)" in result.stdout
    assert "Elapsed time at interruption:" in result.stdout
    assert "ERROR: Interrupted." in result.stderr
    assert result.stdout.index("Elapsed time at interruption:") < result.stdout.index("sandbox cleanup completed")
    assert (tmp_path / "run" / "interrupted.json").is_file()
