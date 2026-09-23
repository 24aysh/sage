from __future__ import annotations

import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from sage.domain.embeddings import VectorStatus
from sage.domain.memory import (
    MemoryRetrievalBudgets,
    MemoryRetrievalOutcome,
    MemoryRetrievalStatus,
)
from sage.legion_memory.retrieval import extract_issue_signals, retrieve_issue_context
from sage.legion_memory.service import LegionMemoryService
from sage.legion_memory.store import GraphStore

from .conftest import apply_files, commit_all


def _retrieve(
    fixture_repo: Path,
    built_memory: tuple[LegionMemoryService, Path],
    issue: str,
    *,
    budgets: MemoryRetrievalBudgets | None = None,
):
    service, memory_file = built_memory
    return service.retrieve_issue_context(
        issue_text=issue,
        repo_root=fixture_repo,
        memory_file=memory_file,
        budgets=budgets,
    )


@pytest.mark.parametrize("max_chars", [500, 1500, 4000, 12000])
def test_compact_budget_retains_explicit_behavior_owner(fixture_repo, built_memory, max_chars):
    result = _retrieve(fixture_repo, built_memory, "Fix `Worker.run` using `helper`.",
                       budgets=MemoryRetrievalBudgets(max_chars=max_chars))
    assert result.context_chars == len(result.context) <= max_chars
    assert result.items[0].qualified_name in {"service.py::Worker.run", "service.py::helper"}
    if max_chars >= 4000:
        assert {"service.py::Worker.run", "service.py::helper"} <= {i.qualified_name for i in result.items}


def test_issue_signals_extract_paths_identifiers_and_error_tokens() -> None:
    signals = extract_issue_signals(
        "Fix `helper` in service.py after Worker.run raises ProcessFailure.",
        max_chars=1_000,
    )

    assert signals.paths == ("service.py",)
    assert {"helper", "worker.run", "processfailure"}.issubset(signals.identifiers)
    assert "service" in signals.terms


@pytest.mark.parametrize(
    ("issue", "expected_path", "expected_reason"),
    [
        ("The `helper` function returns the wrong value.", "service.py", "exact_identifier"),
        ("Correct the implementation in service.py.", "service.py", "path_match"),
        ("Worker run execution returns an incorrect result.", "service.py", "exact_identifier"),
    ],
)
def test_retrieval_ranks_expected_lexical_memory(
    fixture_repo: Path,
    built_memory: tuple[LegionMemoryService, Path],
    issue: str,
    expected_path: str,
    expected_reason: str,
) -> None:
    result = _retrieve(fixture_repo, built_memory, issue)

    assert result.status is MemoryRetrievalStatus.USED
    assert result.returned > 0
    assert any(item.file_path == expected_path for item in result.items[:3])
    assert any(expected_reason in item.reasons for item in result.items[:3])
    assert result.search_modes != ("none",)
    assert result.context.startswith(
        "Graph-derived navigation context. Verify locations and behavior against source."
    )
    assert len(result.context) == result.context_chars <= 12_000
    assert result.duration_ms < 1_000


def test_graph_expansion_adds_callers_tests_flows_or_communities(
    fixture_repo: Path,
    built_memory: tuple[LegionMemoryService, Path],
) -> None:
    result = _retrieve(
        fixture_repo,
        built_memory,
        "The `helper` function returns the wrong value.",
    )
    expanded = [item for item in result.items if item.relationships]
    reasons = {reason for item in expanded for reason in item.reasons}

    assert result.expanded_candidates > 0
    assert {"caller_of", "test_for", "same_flow", "same_community"} & reasons
    assert any(item.is_test or item.name == "test_helper" for item in expanded)
    assert all(item.relationships[0].seed_qualified_name for item in expanded)


def test_graph_expansion_preserves_lexical_top_rank(
    fixture_repo: Path,
    built_memory: tuple[LegionMemoryService, Path],
) -> None:
    _, memory_file = built_memory
    with GraphStore(memory_file, read_only=True) as store:
        lexical_rows, _ = store.search("helper", kind=None, limit=12)
    lexical_names = {str(item["qualified_name"]) for item in lexical_rows}
    result = _retrieve(fixture_repo, built_memory, "Fix `helper`.")
    expanded_names = {item.qualified_name for item in result.items}
    expected = {"service.py::helper", "service.py::Worker.run"}
    lexical_recall = len(expected & lexical_names) / len(expected)
    expanded_recall = len(expected & expanded_names) / len(expected)
    helper_rank = next(item.rank for item in result.items if item.name == "helper")
    reciprocal_rank = 1 / helper_rank

    assert helper_rank <= 2
    assert reciprocal_rank >= 0.5
    assert expanded_recall >= lexical_recall
    assert expanded_recall == 1.0
    assert result.lexical_candidates >= 1
    assert result.total_candidates >= result.lexical_candidates


def test_explicit_path_disambiguates_duplicate_symbol_names(
    fixture_repo: Path,
    built_memory: tuple[LegionMemoryService, Path],
) -> None:
    service, memory_file = built_memory
    (fixture_repo / "other.py").write_text(
        "def helper():\n    return 'other'\n",
        encoding="utf-8",
    )
    commit_all(fixture_repo, "add duplicate helper")
    service.build_or_update_graph_tool(
        repo_root=fixture_repo,
        memory_file=memory_file,
    )

    result = service.retrieve_issue_context(
        issue_text="Fix `helper` in service.py.",
        repo_root=fixture_repo,
        memory_file=memory_file,
    )

    matching = [item for item in result.items if item.name == "helper"]
    assert len(matching) >= 2
    assert matching[0].file_path == "service.py"


def test_locked_database_returns_unavailable_within_query_budget(
    fixture_repo: Path,
    built_memory: tuple[LegionMemoryService, Path],
    tmp_path: Path,
) -> None:
    service, source = built_memory
    memory_file = tmp_path / "locked.sqlite3"
    shutil.copy2(source, memory_file)
    with sqlite3.connect(memory_file) as connection:
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("BEGIN EXCLUSIVE")

        result = service.retrieve_issue_context(
            issue_text="Fix `helper`.",
            repo_root=fixture_repo,
            memory_file=memory_file,
        )

    assert result.status is MemoryRetrievalStatus.UNAVAILABLE
    assert result.duration_ms < 1_000


def test_irrelevant_and_unsupported_language_terms_return_no_match(
    fixture_repo: Path,
    built_memory: tuple[LegionMemoryService, Path],
) -> None:
    irrelevant = _retrieve(
        fixture_repo,
        built_memory,
        "QuasarNebulaZXQ has unrelated frobnication behavior.",
    )
    unsupported = _retrieve(
        fixture_repo,
        built_memory,
        "Update the prose in README.md for lunar deployment.",
    )

    assert irrelevant.status is MemoryRetrievalStatus.NO_MATCH
    assert irrelevant.outcome is MemoryRetrievalOutcome.NO_LEXICAL_CANDIDATES
    assert irrelevant.items == ()
    assert unsupported.status is MemoryRetrievalStatus.NO_MATCH


def test_css_selector_is_available_to_issue_retrieval(tmp_path: Path) -> None:
    with GraphStore(tmp_path / "graph.sqlite3") as store:
        apply_files(store, {
            "index.html": '<button class="checkout-button">Buy</button>',
            "site.css": ".checkout-button { color: blue; }",
        })
        result = retrieve_issue_context(
            "Fix the `.checkout-button` styles.", store,
            memory_file=store.path, budgets=MemoryRetrievalBudgets(),
        )

    assert result.status is MemoryRetrievalStatus.USED
    assert any(item.kind == "Selector" and item.name == ".checkout-button"
               for item in result.items)


def test_candidates_below_threshold_are_distinguished_from_no_candidates(
    fixture_repo: Path,
    built_memory: tuple[LegionMemoryService, Path],
) -> None:
    result = _retrieve(
        fixture_repo,
        built_memory,
        "Fix `helper`.",
        budgets=MemoryRetrievalBudgets(usefulness_threshold=100.0),
    )

    assert result.status is MemoryRetrievalStatus.NO_MATCH
    assert result.outcome is MemoryRetrievalOutcome.BELOW_THRESHOLD
    assert result.lexical_candidates > 0
    assert result.returned == 0


def test_result_and_character_budgets_truncate_deterministically(
    fixture_repo: Path,
    built_memory: tuple[LegionMemoryService, Path],
) -> None:
    budgets = MemoryRetrievalBudgets(max_results=1, max_chars=500)
    first = _retrieve(fixture_repo, built_memory, "Fix `helper`.", budgets=budgets)
    second = _retrieve(fixture_repo, built_memory, "Fix `helper`.", budgets=budgets)

    assert first.status is MemoryRetrievalStatus.USED
    assert first.outcome is MemoryRetrievalOutcome.USEFUL_CONTEXT_TRUNCATED
    assert first.returned == 1
    assert first.omitted == first.total_candidates - 1
    assert first.context_chars <= 500
    assert [item.qualified_name for item in first.items] == [
        item.qualified_name for item in second.items
    ]


def test_stale_graph_is_unavailable_instead_of_exposing_memory(
    fixture_repo: Path,
    built_memory: tuple[LegionMemoryService, Path],
) -> None:
    (fixture_repo / "README.md").write_text("new base\n", encoding="utf-8")
    commit_all(fixture_repo, "advance repository")

    result = _retrieve(fixture_repo, built_memory, "Fix `helper`.")

    assert result.status is MemoryRetrievalStatus.UNAVAILABLE
    assert result.outcome is MemoryRetrievalOutcome.GRAPH_UNAVAILABLE
    assert result.items == ()
    assert result.context == ""


@pytest.mark.parametrize("database_state", ["missing", "corrupt", "unsupported"])
def test_invalid_databases_return_bounded_unavailable_results(
    fixture_repo: Path,
    built_memory: tuple[LegionMemoryService, Path],
    tmp_path: Path,
    database_state: str,
) -> None:
    service, source = built_memory
    memory_file = tmp_path / f"{database_state}.sqlite3"
    if database_state == "corrupt":
        memory_file.write_bytes(b"not sqlite")
    elif database_state == "unsupported":
        shutil.copy2(source, memory_file)
        with sqlite3.connect(memory_file) as connection:
            connection.execute(
                "UPDATE metadata SET value='999' WHERE key='schema_version'"
            )
            connection.commit()

    result = service.retrieve_issue_context(
        issue_text="Fix `helper`.",
        repo_root=fixture_repo,
        memory_file=memory_file,
    )

    assert result.status is MemoryRetrievalStatus.UNAVAILABLE
    assert result.outcome is MemoryRetrievalOutcome.GRAPH_UNAVAILABLE
    assert len(result.summary) <= 500
    assert result.returned == 0


def test_foreign_repository_graph_is_not_exposed(
    fixture_repo: Path,
    built_memory: tuple[LegionMemoryService, Path],
    tmp_path: Path,
) -> None:
    service, memory_file = built_memory
    foreign = tmp_path / "foreign"
    shutil.copytree(fixture_repo, foreign)
    subprocess_result = subprocess.run(
        ["git", "-C", str(foreign), "remote", "add", "origin", "foreign.invalid/repo"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert subprocess_result.returncode == 0

    result = service.retrieve_issue_context(
        issue_text="Fix `helper`.",
        repo_root=foreign,
        memory_file=memory_file,
    )

    assert result.status is MemoryRetrievalStatus.UNAVAILABLE
    assert "different repository" in result.summary


def test_adversarial_issue_text_remains_parameterized_and_bounded(
    fixture_repo: Path,
    built_memory: tuple[LegionMemoryService, Path],
) -> None:
    result = _retrieve(
        fixture_repo,
        built_memory,
        "`helper' OR 1=1; DROP TABLE nodes; --` ../../service.py \x1b[31m",
    )
    service, memory_file = built_memory
    stats = service.graph_stats(repo_root=fixture_repo, memory_file=memory_file)

    assert result.status in {MemoryRetrievalStatus.USED, MemoryRetrievalStatus.NO_MATCH}
    assert stats.nodes > 0
    assert len(result.context) <= 12_000


def test_invalid_node_paths_are_never_returned(
    fixture_repo: Path,
    built_memory: tuple[LegionMemoryService, Path],
) -> None:
    service, memory_file = built_memory
    with sqlite3.connect(memory_file) as connection:
        connection.execute(
            "UPDATE nodes SET file_path='../escape.py' WHERE name='helper'"
        )
        connection.commit()

    result = service.retrieve_issue_context(
        issue_text="Fix `helper`.",
        repo_root=fixture_repo,
        memory_file=memory_file,
    )

    assert all(".." not in Path(item.file_path).parts for item in result.items)


def test_one_failed_expansion_preserves_primary_hits(
    fixture_repo: Path,
    built_memory: tuple[LegionMemoryService, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sage.legion_memory.retrieval as retrieval

    def fail_expansion(*_args, **_kwargs):
        raise sqlite3.OperationalError("synthetic expansion failure")

    monkeypatch.setattr(retrieval, "_expand_edges", fail_expansion)
    result = _retrieve(fixture_repo, built_memory, "Fix `helper`.")

    assert result.status is MemoryRetrievalStatus.USED
    assert any(item.name == "helper" for item in result.items)
    assert result.warnings


def test_semantic_distractor_does_not_evict_explicit_symbol(tmp_path: Path):
    class Vectors:
        def search(self, store, query, *, limit):
            assert len(query) <= 1200
            return [("config.py::settings", .8)], VectorStatus(status="ready")

    with GraphStore(tmp_path / "graph.sqlite3") as store:
        apply_files(store, {"service.py": "class WebhookService:\n    def process(self):\n        pass\n",
                      "config.py": "def settings():\n    return 1\n"})
        result = retrieve_issue_context("Fix `WebhookService.process` retry handling.", store,
            memory_file=store.path, budgets=MemoryRetrievalBudgets(), vectors=Vectors())
        assert result.items[0].qualified_name == "service.py::WebhookService.process"
        assert "semantic" in result.search_modes
        assert any(d.channel_ranks.get("lexical") for d in result.diagnostics)
        assert result.duration_ms >= result.ranking_duration_ms


def test_unresolved_edge_is_not_rebound_to_a_test_double(tmp_path: Path):
    with GraphStore(tmp_path / "graph.sqlite3") as store:
        apply_files(store, {"repo.py": "class Repo:\n    def claim(self):\n        self.collection.insert_one({})\n",
                      "tests/fakes.py": "class FakeCollection:\n    def insert_one(self, data):\n        pass\n"})
        assert store.node("insert_one") is not None
        assert store.exact_node("insert_one") is None
        result = retrieve_issue_context("Fix `Repo.claim`.", store,
            memory_file=store.path, budgets=MemoryRetrievalBudgets())
        assert not any(i.name == "insert_one" and any(r.relationship == "CALLS" for r in i.relationships)
                       for i in result.items)
        assert result.unresolved_edges > 0
