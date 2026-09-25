from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from sage.config import JevSettings
from sage.domain.relevance import RelevanceDecision, RelevanceUnavailable
from sage.harness.jev.filter import RelevanceFilter
from sage.harness.retrieval.service import RepositoryRetrievalService

from evals.retrieval.runner import prepare_evaluation, run_evaluation
from evals.retrieval.models import EvaluationResults


def _git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    repository = tmp_path / "repo"
    repository.mkdir()
    _git(repository, "init", "--initial-branch=main")
    _git(repository, "config", "user.name", "Eval Test")
    _git(repository, "config", "user.email", "eval@example.invalid")
    (repository / "app.py").write_text(
        "def helper():\n    return 1\n", encoding="utf-8"
    )
    (repository / "noise.py").write_text(
        "def unrelated():\n    return 2\n", encoding="utf-8"
    )
    _git(repository, "add", "--all")
    _git(repository, "commit", "-m", "fixture")
    graph = tmp_path / "graph.sqlite3"
    RepositoryRetrievalService().build_or_update_graph_tool(
        repo_root=repository, index_file=graph
    )
    issues = tmp_path / "issues"
    issues.mkdir()
    (issues / "issue-1.md").write_text(
        "Fix the helper function in app.py", encoding="utf-8"
    )
    (issues / "correct.json").write_text(
        json.dumps({"issue_1": ["app.py"]}), encoding="utf-8"
    )
    return repository, graph, issues


class Provider:
    model = "jev-test"
    capture = None

    def __init__(self) -> None:
        self.calls = []
        self.closed = False

    async def score_files(self, *, state, candidates, timeout):
        self.calls.append((state, candidates, timeout))
        scores = {candidate.id: (3.0 if candidate.path == "app.py" else 0.0) for candidate in candidates}
        return RelevanceDecision(
            model=self.model,
            scores=scores,
            confidences={candidate.id: .9 for candidate in candidates},
            input_tokens=10,
            output_tokens=5,
        )

    async def aclose(self) -> None:
        self.closed = True


class Progress:
    def __init__(self) -> None:
        self.updates = 0
        self.postfix = ""

    def update(self, amount=1):
        self.updates += amount

    def set_postfix_str(self, value):
        self.postfix = value


class CancellingProvider(Provider):
    async def score_files(self, *, state, candidates, timeout):
        self.calls.append((state, candidates, timeout))
        raise asyncio.CancelledError


class UnavailableProvider(Provider):
    async def score_files(self, *, state, candidates, timeout):
        self.calls.append((state, candidates, timeout))
        raise RelevanceUnavailable("http_429")


def test_real_retrieval_and_production_filter_are_replayed_once(tmp_path: Path) -> None:
    repository, graph, issues = _fixture(tmp_path)
    output = tmp_path / "output"
    prepared = prepare_evaluation(
        repo=repository,
        issues_dir=issues,
        issue_count=1,
        graph=graph,
        output_dir=output,
        environment={"TYPESAFE_API_KEY": "test-key", "SAGE_JEV_LOG_INPUT": "false"},
    )
    provider = Provider()
    relevance = RelevanceFilter(
        settings=JevSettings(
            mode="on", api_key="test-key", model=provider.model, timeout_seconds=2
        ),
        provider=provider,
    )
    progress = Progress()

    results, exit_code = asyncio.run(
        run_evaluation(prepared, relevance_filter=relevance, progress=progress)
    )

    assert exit_code == 0
    assert results.status == "complete"
    assert len(provider.calls) == 1
    assert provider.calls[0][0]["issue"] == "Fix the helper function in app.py"
    assert "gold_files" not in provider.calls[0][0]
    assert provider.closed
    assert progress.updates == 1
    assert results.issues[0].status == "judged"
    assert results.issues[0].accepted_files == ("app.py",)
    assert (output / "graph.sqlite3").is_file()
    assert (output / "issues/issue-1.json").is_file()
    assert (output / "evals.md").is_file()


def test_preflight_forces_on_without_mutating_environment(tmp_path: Path) -> None:
    repository, graph, issues = _fixture(tmp_path)
    environment = {
        "TYPESAFE_API_KEY": "test-key",
        "SAGE_JEV_NAVIGATION_MODE": "off",
        "SAGE_JEV_LOG_INPUT": "false",
    }

    prepared = prepare_evaluation(
        repo=repository,
        issues_dir=issues,
        issue_count=1,
        graph=graph,
        output_dir=tmp_path / "output",
        environment=environment,
    )

    assert prepared.settings.mode == "on"
    assert environment["SAGE_JEV_NAVIGATION_MODE"] == "off"


def test_cancellation_checkpoints_raw_observation_and_partial_report(tmp_path: Path) -> None:
    repository, graph, issues = _fixture(tmp_path)
    (issues / "issue-2.md").write_text("Fix helper again", encoding="utf-8")
    (issues / "correct.json").write_text(
        json.dumps({"issue_1": ["app.py"], "issue_2": ["app.py"]}),
        encoding="utf-8",
    )
    output = tmp_path / "output"
    prepared = prepare_evaluation(
        repo=repository,
        issues_dir=issues,
        issue_count=2,
        graph=graph,
        output_dir=output,
        environment={"TYPESAFE_API_KEY": "test-key", "SAGE_JEV_LOG_INPUT": "false"},
    )
    provider = CancellingProvider()
    relevance = RelevanceFilter(
        settings=JevSettings(
            mode="on", api_key="test-key", model=provider.model, timeout_seconds=2
        ),
        provider=provider,
    )
    progress = Progress()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run_evaluation(prepared, relevance_filter=relevance, progress=progress))

    saved = EvaluationResults.model_validate_json(
        (output / "results.json").read_text(encoding="utf-8")
    )
    assert saved.status == "partial"
    assert saved.issues[0].status == "interrupted"
    assert saved.issues[0].raw_files
    assert saved.issues[0].metrics.raw_correct_recall_pct == 100
    assert saved.issues[0].metrics.noise_after_jev_pct is None
    assert saved.issues[1].status == "not_run"
    assert saved.issues[1].reason == "interrupted"
    saved_second = json.loads((output / "issues/issue-2.json").read_text())
    assert saved_second["status"] == "not_run"
    assert provider.closed
    assert progress.updates == 1


def test_unavailable_filter_preserves_raw_metrics_without_claiming_rejection(tmp_path: Path) -> None:
    repository, graph, issues = _fixture(tmp_path)
    prepared = prepare_evaluation(
        repo=repository,
        issues_dir=issues,
        issue_count=1,
        graph=graph,
        output_dir=tmp_path / "output",
        environment={"TYPESAFE_API_KEY": "test-key", "SAGE_JEV_LOG_INPUT": "false"},
    )
    provider = UnavailableProvider()
    relevance = RelevanceFilter(
        settings=JevSettings(
            mode="on", api_key="test-key", model=provider.model, timeout_seconds=2
        ),
        provider=provider,
    )

    results, exit_code = asyncio.run(
        run_evaluation(prepared, relevance_filter=relevance)
    )

    issue = results.issues[0]
    assert exit_code == 1
    assert results.status == "partial"
    assert issue.status == "filter_unavailable"
    assert issue.reason == "http_429"
    assert issue.rejected_files == ()
    assert issue.withheld_files == issue.raw_files
    assert issue.metrics.noise_before_pct is not None
    assert issue.metrics.noise_after_jev_pct is None
