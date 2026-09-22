"""Navigation reuses validated graph queries, filtering and exposure accounting."""

import asyncio
from types import SimpleNamespace

from sage.artifacts.store import RunArtifacts
from sage.config import JevSettings
from sage.domain.navigation import NavigationDecision
from sage.orchestration.navigation import NavigationSession
from sage.providers.calls import ModelCalls
from sage.repository.service import Repository


def test_graph_to_read_uses_actual_retained_nodes(memory_session, fixture_repo, tmp_path, settings):
    config = settings.model_copy(update={"jev": JevSettings(mode="on", policy="actions", api_key="test",
        max_followup_actions=2), "max_tool_output_chars": 12000})
    repository = Repository(workspace_root=fixture_repo, sandbox=None, settings=config)
    captures = []

    class Provider:
        model = "jev-1.13.0"
        capture = None

        async def choose_action(self, *, state, candidates, timeout):
            captures.append(candidates)
            if len(captures) == 1:
                selected = next(c for c in candidates if c.action.kind == "query_graph_tool"
                                and c.action.target.endswith("::helper"))
            else:
                selected = next(c for c in candidates if c.action.kind == "read_file")
            return NavigationDecision(model=self.model, selected=(selected.id,),
                                      probabilities={selected.id: .99}, confidence=.99)

    # Consume structural facts in this history, leaving the shared budget for the selected operation.
    memory_session.begin_session(initial_visible=True)
    memory_session.enrich(tool_name="read_file", path="service.py", start_line=1, end_line=9, available_chars=3000)
    context = SimpleNamespace(settings=config, repository=repository, memory=memory_session,
        prepared_run=SimpleNamespace(workspace_dir=fixture_repo), artifacts=RunArtifacts(tmp_path / "artifacts"))
    nav = NavigationSession(context=context, provider=Provider(), calls=ModelCalls(settings=config, reviewer=None),
                            issue="Find callers of helper", plan=lambda: None)
    source = repository.read_file(path="service.py")
    output = asyncio.run(nav.enrich(tool_name="read_file", source=source, path="service.py",
                                   exploration_goal="Find callers of helper and inspect source"))
    assert len(captures) == 2
    assert output.count("<navigation-observation") == 2
    assert memory_session.tool_calls[-1].tool_name == "navigation:query_graph_tool"
    assert len(output) <= 3000
    assert nav.operations == 2


def test_graph_invalidation_and_shared_structural_budget(memory_session, fixture_repo, tmp_path, settings):
    config = settings.model_copy(update={"jev": JevSettings(mode="on", policy="actions", api_key="test"),
                                         "max_tool_output_chars": 12000})
    context = SimpleNamespace(settings=config, repository=None, memory=memory_session,
        prepared_run=SimpleNamespace(workspace_dir=fixture_repo), artifacts=RunArtifacts(tmp_path / "artifacts"))
    nav = NavigationSession(context=context, provider=SimpleNamespace(model="jev-1.13.0"),
        calls=ModelCalls(settings=config, reviewer=None), issue="helper", plan=lambda: None)
    nav.session_chars = 15900
    result = asyncio.run(nav.enrich(tool_name="read_file", source="8 | def helper():", path="service.py"))
    assert len(result) <= 100
    nav.invalidate("service.py")
    assert all(n["file_path"] != "service.py" for n in nav._nodes())
    nav.invalidate()
    assert nav._nodes() == []
