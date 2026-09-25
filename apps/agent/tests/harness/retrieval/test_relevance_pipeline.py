"""CLI and solve consume the same filtered graph evidence, once per Issue."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from sage.artifacts.store import RunArtifacts
from sage.cli.app import main
from sage.config import JevSettings, Settings
from sage.domain.relevance import RelevanceDecision
from sage.domain.solve import PreparedRun
from sage.harness.context.run import SolveContext
from sage.harness.jev.filter import RelevanceFilter
from sage.harness.retrieval.session import RetrievalSession
from sage.orchestration.solve import SolveOrchestrator


class Provider:
    model = "jev-1.13.0"
    capture = None

    def __init__(self):
        self.requests = 0
        self.closed = False

    async def score_files(self, *, candidates, **kwargs):
        self.requests += 1
        return RelevanceDecision(model=self.model,
            scores={c.id: 3 if c.path == "service.py" else 0 for c in candidates},
            confidences={c.id: 1 for c in candidates}, input_tokens=120, output_tokens=10)

    async def aclose(self):
        self.closed = True


@pytest.mark.parametrize("failure", [False, True])
def test_cli_publishes_only_accepted_files_and_discard_counts(fixture_repo, built_index, tmp_path, monkeypatch, capsys, failure):
    service, database = built_index
    issue = tmp_path / "issue.md"
    issue.write_text("Fix `Worker.run` using `helper`.")
    provider = Provider()
    if failure:
        from sage.domain.relevance import RelevanceUnavailable
        async def unavailable(**kwargs):
            provider.requests += 1
            raise RelevanceUnavailable("http_529")
        provider.score_files = unavailable
    settings = JevSettings(mode="on", api_key="test")
    monkeypatch.setattr("sage.cli.retrieval.JevSettings.from_env", lambda: settings)
    monkeypatch.setattr("sage.cli.retrieval.build_retrieval_service", lambda: service)
    monkeypatch.setattr("sage.cli.retrieval.build_relevance_filter",
                        lambda config: RelevanceFilter(settings=config, provider=provider))
    exit_code = main(["retrieval", "retrieve", "--repo", str(fixture_repo), "--issue-file", str(issue),
                      "--index-file", str(database)])
    assert exit_code == (1 if failure else 0)
    output = capsys.readouterr().out
    report = json.loads(database.with_suffix(".relevance.json").read_text())
    retrieval = json.loads(database.with_suffix(".retrieval.json").read_text())
    if failure:
        assert report["retained_files"] == [] and report["withheld_items"] > 0
        assert report["discarded_items"] == 0
        assert retrieval["context"] == "" and not retrieval["items"]
        assert "Unjudged/withheld:" in output
        assert provider.requests == 1 and provider.closed
        return
    assert report["retained_files"] == ["service.py"]
    assert report["discarded_items"] > 0
    assert "Relevant files after Jev filter: 1" in output
    assert f'Discarded by Jev: {report["discarded_items"]} retrieval items' in output
    assert all(item["file_path"] == "service.py" for item in retrieval["items"])
    assert provider.requests == 1 and provider.closed


def test_solve_filters_before_prompt_and_preserves_usage_and_repair_locators(fixture_repo, built_index, tmp_path):
    service, database = built_index
    build = service.build_or_update_graph_tool(repo_root=fixture_repo, index_file=database)
    issue = "Fix `Worker.run` using `helper`."
    retrieval = service.retrieve_issue_context(issue_text=issue, repo_root=fixture_repo, index_file=database)
    session = RetrievalSession(service, fixture_repo, database, database, build, retrieval)
    config = Settings(openai_api_key="test", jev=JevSettings(mode="on", api_key="test"))
    prepared = PreparedRun(run_id="filter-test", source_repo=fixture_repo, workspace_dir=fixture_repo,
        run_dir=tmp_path / "run", base_sha=build.indexed_sha, base_ref="HEAD")
    artifacts = RunArtifacts(prepared.run_dir)
    context = SolveContext(prepared_run=prepared, repository=SimpleNamespace(get_changed_files=lambda: ()), settings=config,
                           artifacts=artifacts, retrieval=session)
    provider = Provider()

    async def inspect(**kwargs):
        assert "service.py" in kwargs["message"]
        assert "tests/test_service.py" not in kwargs["message"]
        assert not kwargs["context"].retrieval.enrichment_enabled
        assert kwargs["context"].retrieval.enrich(tool_name="read_file", available_chars=2000, path="app.py") == ""
        repair = session.begin_session(initial_visible=False)
        assert "service.py" in repair and "tests/test_service.py" not in repair
        session.invalidate("service.py")
        assert session.begin_session(initial_visible=False) == ""
        raise RuntimeError("stop after inspecting context")

    engine = SolveOrchestrator(solver=SimpleNamespace(run=inspect), reviewer=None, reviewer_provider=None,
        relevance_filter_factory=lambda run_id: RelevanceFilter(settings=config.jev, provider=provider))
    with pytest.raises(RuntimeError, match="stop after inspecting"):
        asyncio.run(engine.solve(issue_text=issue, context=context))
    usage = json.loads((prepared.run_dir / "usage.json").read_text())
    assert usage["semantic_calls"][0]["input_tokens"] == 120
    assert usage["semantic_calls"][0]["stage"] == "solver-context"
    assert usage["agent_timings"][0]["stage"] == "solver-context"
    assert usage["agent_timings"][0]["duration_ms"] >= usage["semantic_calls"][0]["latency_ms"]
    assert json.loads((prepared.run_dir / "relevance-filter.json").read_text())["retained_files"] == ["service.py"]
    assert provider.requests == 1 and provider.closed
