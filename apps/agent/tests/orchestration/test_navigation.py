"""Bounded navigation using real temporary source reads and scripted judgments."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import ToolMessage

from sage.agents.repository_tools import build_repository_read_tools
from sage.artifacts.store import RunArtifacts
from sage.config import JevSettings
from sage.domain.navigation import NavigationDecision, NavigationUnavailable, SearchMatch, SearchResult
from sage.errors import RepositoryError
from sage.orchestration.navigation import NavigationSession
from sage.providers.calls import ModelCalls
from sage.repository.service import Repository


class Provider:
    model = "jev-1.13.0"
    capture = None

    def __init__(self, choices):
        self.choices, self.requests, self.closed = iter(choices), [], False

    async def choose_action(self, *, state, candidates, timeout):
        self.requests.append((state, candidates, timeout))
        choice = next(self.choices, None)
        if isinstance(choice, BaseException):
            raise choice
        if callable(choice):
            choice = choice(candidates)
        selected = next((c.id for c in candidates if c.action.kind == choice), choice)
        return NavigationDecision(model=self.model, selected=(selected,) if selected else (),
            probabilities={selected: .95} if selected else {}, confidence=.9, input_tokens=100, output_tokens=10)

    async def rank_excerpts(self, *, state, candidates, timeout):
        self.requests.append((state, candidates, timeout))
        return NavigationDecision(model=self.model, selected=tuple(c.id for c in candidates[:2]),
                                  scores={c.id: 2.9 for c in candidates},
                                  confidences={c.id: .9 for c in candidates}, input_tokens=100)

    async def aclose(self):
        self.closed = True


@pytest.fixture
def setup_navigation(tmp_path, settings):
    (tmp_path / "app.py").write_text("def calculate():\n    return 42\n")
    (tmp_path / "tests.py").write_text("def test_calculate():\n    assert calculate() == 42\n")

    def make(choices=(), *, mode="on", policy="actions", steps=2, cap=12000):
        config = settings.model_copy(update={"jev": JevSettings(mode=mode, policy=policy,
            api_key="test", max_followup_actions=steps), "max_tool_output_chars": cap})
        repository = Repository(workspace_root=tmp_path, sandbox=None, settings=config)
        searches = []

        def search(**arguments):
            searches.append(arguments)
            match = SearchMatch(path="tests.py", line=2, column=12, text="    assert calculate() == 42")
            return SearchResult(text="tests.py:2:12:    assert calculate() == 42", matches=(match,))
        repository.search_matches = search
        calls = ModelCalls(settings=config, reviewer=None)
        provider = Provider(choices)
        context = SimpleNamespace(settings=config, repository=repository, memory=None,
            prepared_run=SimpleNamespace(workspace_dir=tmp_path), artifacts=RunArtifacts(tmp_path / "artifacts"))
        nav = NavigationSession(context=context, provider=provider, calls=calls, issue="Fix calculate",
                                plan=lambda: None)
        nav.begin_session(stage="solver")
        context.navigation = nav
        return nav, context, provider, calls, searches
    return make


def root_search(nav, **kwargs):
    return asyncio.run(nav.enrich(tool_name="search_text", source="app.py:1:1:def calculate():",
        query="calculate", path=".", matches=(SearchMatch(path="app.py", line=1, column=1,
        text="def calculate():"),), exploration_goal="Find implementation and callers", **kwargs))


def test_two_dependent_actions_and_one_tool_message(setup_navigation):
    nav, context, provider, calls, searches = setup_navigation(["search_text", "read_file"])
    tools = {t.name: t for t in build_repository_read_tools(context)}
    result = asyncio.run(tools["read_file"].ainvoke({"name": "read_file", "id": "root-id", "type": "tool_call",
        "args": {"path": "app.py", "exploration_goal": "Find callers and read the test"}}))
    assert isinstance(result, ToolMessage) and result.tool_call_id == "root-id"
    assert result.content.startswith("1 | def calculate():")
    assert result.content.count("<navigation-observation") == 2
    assert '"path": "tests.py"' in result.content
    assert len(provider.requests) == 2 and len(calls.provenance().semantic_calls) == 2
    first, second = provider.requests
    assert not any(getattr(c.action, "path", None) == "tests.py" for c in first[1])
    assert any(getattr(c.action, "path", None) == "tests.py" for c in second[1])
    assert second[0]["observations"]
    assert searches == [{"query": "calculate", "path": ".", "max_results": 5, "timeout_seconds": 2}]
    assert not calls.provenance().tool_calls  # internal work creates no model tool calls


@pytest.mark.parametrize("mode,steps,requests,operations", [("on", 1, 1, 1), ("on", 2, 2, 1), ("shadow", 2, 1, 0)])
def test_step_limit_handback_and_shadow(setup_navigation, mode, steps, requests, operations):
    nav, _, provider, _, _ = setup_navigation(["read_file", None], mode=mode, steps=steps)
    result = root_search(nav)
    # Reading calculate produces no new declaration distinct from the root query.
    assert len(provider.requests) <= requests
    assert nav.operations == operations
    assert ("return 42" in result) == (mode == "on")


def test_second_failure_preserves_first_and_unknown_usage(setup_navigation):
    nav, context, provider, calls, _ = setup_navigation(["search_text", NavigationUnavailable("http_529")])
    source = context.repository.read_file(path="app.py")
    result = asyncio.run(nav.enrich(tool_name="read_file", source=source, path="app.py", exploration_goal="Find callers"))
    assert "tests.py:2:12:" in result
    assert nav.operations == 1 and len(provider.requests) == 2
    assert calls.provenance().semantic_calls[-1].input_tokens is None


def test_second_capability_failure_preserves_first(setup_navigation):
    nav, context, _, _, _ = setup_navigation(["search_text", "read_file"])
    source = context.repository.read_file(path="app.py")
    context.repository.read_file = lambda **kw: (_ for _ in ()).throw(RepositoryError("gone"))
    result = asyncio.run(nav.enrich(tool_name="read_file", source=source, path="app.py", exploration_goal="Find callers"))
    assert "tests.py:2:12:" in result and "optional observation unavailable" in result
    assert nav.operations == 2  # failed attempt consumes a slot


@pytest.mark.parametrize("failure,expected", [(NavigationUnavailable("http_401", permanent=True), 1),
                                             (NavigationUnavailable("http_529"), 2)])
def test_circuit_breaker_no_retries(setup_navigation, failure, expected):
    nav, _, provider, calls, _ = setup_navigation([failure] * 10)
    for _ in range(5):
        assert root_search(nav) == ""
    assert len(provider.requests) == expected
    assert all(c.input_tokens is None for c in calls.provenance().semantic_calls)


def test_request_budget_persists_across_repairs(setup_navigation):
    nav, _, provider, _, _ = setup_navigation([None] * 20)
    for session in range(3):
        nav.begin_session(stage="solver-repair" if session else "solver")
        for _ in range(6):
            root_search(nav)
    assert len(provider.requests) == 8


def test_source_dedup_and_mutation_repair_invalidation(setup_navigation):
    nav, context, provider, _, _ = setup_navigation(["read_file"] * 4, steps=1)
    assert "return 42" in root_search(nav)
    assert root_search(nav) == ""
    assert len(provider.requests) == 1
    (context.prepared_run.workspace_dir / "app.py").write_text("def calculate():\n    return 43\n")
    nav.invalidate("app.py")
    assert "return 43" in root_search(nav)
    nav.begin_session(stage="solver-repair")
    assert "return 43" in root_search(nav)


def test_no_goal_no_room_no_time_mean_no_inference(setup_navigation):
    nav, _, provider, calls, _ = setup_navigation(["read_file"])
    assert asyncio.run(nav.enrich(tool_name="read_file", source="1 | def calculate():", path="app.py")) == ""
    nav.session_chars = 16000
    assert root_search(nav) == ""
    nav.session_chars = 0
    calls.remaining_navigation_seconds = lambda: 0
    assert root_search(nav) == ""
    assert not provider.requests


def test_cancellation_propagates_and_is_accounted(setup_navigation):
    nav, _, _, calls, _ = setup_navigation([asyncio.CancelledError()])
    with pytest.raises(asyncio.CancelledError):
        root_search(nav)
    assert calls.provenance().semantic_calls[-1].outcome == "cancelled"
    assert nav.operations == 0


def test_freshness_rechecked_after_inference(setup_navigation):
    nav, context, provider, _, _ = setup_navigation([])

    def change(candidates):
        (context.prepared_run.workspace_dir / "app.py").write_text("changed")
        return candidates[0].id
    provider.choices = iter([change])
    assert "unavailable" in root_search(nav)
    assert nav.operations == 0


def test_off_and_excerpt_schemas_unchanged(setup_navigation):
    nav, context, _, _, _ = setup_navigation(policy="excerpts")
    enabled = {t.name: t.args for t in build_repository_read_tools(context)}
    context.navigation = None
    disabled = {t.name: t.args for t in build_repository_read_tools(context)}
    assert enabled == disabled
    nav.action_policy = True
    context.navigation = nav
    action = {t.name: t.args for t in build_repository_read_tools(context)}
    assert "exploration_goal" in action["read_file"] and "exploration_goal" in action["search_text"]


def test_artifact_omits_raw_goals_and_source_by_default(setup_navigation):
    nav, context, _, _, _ = setup_navigation(["read_file"], steps=1)
    root_search(nav)
    data = (context.prepared_run.workspace_dir / "artifacts/navigation.json").read_text()
    assert "Find implementation and callers" not in data and "return 42" not in data
    assert json.loads(data)["operations"] == 1


def test_unsafe_and_oversized_source_never_reaches_provider(setup_navigation):
    nav, context, provider, _, _ = setup_navigation(["read_file"])
    (context.prepared_run.workspace_dir / "app.py").write_bytes(b"x" * 1_048_577)
    assert root_search(nav) == ""
    assert not provider.requests


def test_excerpt_bundle_selects_two_and_shares_output_budget(setup_navigation):
    nav, context, provider, _, _ = setup_navigation(policy="excerpts", cap=1500)
    matches = tuple(SearchMatch(path=p, line=1, column=1, text="def function():") for p in ("app.py", "tests.py"))
    source = "original search evidence\n" * 40
    output = asyncio.run(nav.enrich(tool_name="search_text", source=source, path=".", query="calculate", matches=matches))
    assert len(source + output) <= 1500
    assert nav.operations <= 2 and len(provider.requests) == 1
    assert "<navigation-observation" in output
    assert nav.total_chars == len(output)


def test_excerpt_zero_or_one_candidate_skips_provider(setup_navigation):
    nav, _, provider, _, _ = setup_navigation(policy="excerpts")
    assert root_search(nav) == ""
    assert not provider.requests


def test_failed_root_does_not_start_navigation(setup_navigation):
    nav, context, provider, _, _ = setup_navigation(["read_file"])
    tools = {t.name: t for t in build_repository_read_tools(context)}
    with pytest.raises(RepositoryError):
        asyncio.run(tools["read_file"].ainvoke({"path": "missing.py", "exploration_goal": "Find implementation"}))
    assert not provider.requests and nav.sequence == 0


def test_operation_run_and_session_limits(setup_navigation):
    nav, _, provider, _, _ = setup_navigation(["read_file"] * 20, steps=1)
    for _ in range(3):
        nav.begin_session(stage="solver-repair")
        for _ in range(6):
            nav.visible.clear()
            nav.attempted.clear()
            root_search(nav)
    assert nav.operations == 8 and len(provider.requests) == 8


def test_low_probability_handback_does_not_substitute(setup_navigation):
    nav, _, provider, _, _ = setup_navigation([])

    async def uncertain(**kw):
        return NavigationDecision(model=provider.model, selected=(kw["candidates"][0].id,),
                                  probabilities={kw["candidates"][0].id: .6}, confidence=.4)
    provider.choose_action = uncertain
    assert root_search(nav) == "" and nav.operations == 0


def test_internal_search_respects_real_timeout_and_remaining_reserve(setup_navigation):
    nav, context, provider, calls, searches = setup_navigation(["search_text", "read_file"])
    calls.remaining_navigation_seconds = lambda: 1.2
    source = context.repository.read_file(path="app.py")
    asyncio.run(nav.enrich(tool_name="read_file", source=source, path="app.py", exploration_goal="Find callers"))
    assert searches[0]["timeout_seconds"] == 1
    assert all(request[2] <= 1.2 for request in provider.requests)


def test_mutation_tools_invalidate_visibility(setup_navigation):
    from sage.agents.solver import build_solver_tools
    nav, context, _, _, _ = setup_navigation([])
    context.repository.list_branches = lambda: "main"
    context.repository.switch_branch = lambda **kw: "switched"
    plans = SimpleNamespace(require_implementable=lambda: None)
    tools = {t.name: t for t in build_solver_tools(context, plans)}
    nav.visible[("app.py", "old")] = {1}
    asyncio.run(tools["write_file"].ainvoke({"path": "app.py", "content": "changed", "mode": "replace"}))
    assert not nav.visible and "app.py" in nav.invalidated
    asyncio.run(tools["switch_branch"].ainvoke({"branch_name": "main"}))
    assert nav.graph_disabled


def test_invalidation_normalizes_paths_and_preserves_unchanged_visibility(setup_navigation):
    nav, _, _, _, _ = setup_navigation()
    nav.visible = {("app.py", "old"): {1}, ("tests.py", "same"): {2}}
    nav.invalidate("./app.py")
    assert nav.invalidated == {"app.py"}
    assert nav.visible == {("tests.py", "same"): {2}}
