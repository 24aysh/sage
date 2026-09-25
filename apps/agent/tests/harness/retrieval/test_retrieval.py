from __future__ import annotations

import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from sage.domain.retrieval import (
    RetrievalBudgets,
    RetrievalOutcome,
    RetrievalStatus,
)
from sage.harness.retrieval.ranking import extract_issue_signals, retrieve_issue_context
from sage.harness.retrieval.service import RepositoryRetrievalService
from sage.harness.retrieval.store import GraphStore

from .conftest import apply_files, commit_all


@pytest.mark.parametrize("path,source,symbol,language", [
    ("orders.go", "package shop\nfunc ProcessOrder(customerID int) int { return customerID }\n", "ProcessOrder", "go"),
    ("orders.rs", "pub fn process_order(customer_id: i32) -> i32 { customer_id }\n", "process_order", "rust"),
    ("orders.cpp", "int process_order(int customer_id) { return customer_id; }\n", "process_order", "cpp"),
    ("orders.hxx", "struct OrderRecord { int customer_id; };\n", "OrderRecord", "cpp"),
    ("orders.html", '<main id="order-summary">Order</main>\n', "order-summary", "html"),
    ("orders.css", ".order-summary { color: red; }\n", ".order-summary", "css"),
])
def test_multilingual_symbols_and_paths_are_lexically_retrievable(
    fixture_repo, tmp_path, path, source, symbol, language,
):
    (fixture_repo / path).write_text(source)
    commit_all(fixture_repo, "add language fixture")
    service = RepositoryRetrievalService(data_root=tmp_path / "languages")
    build = service.build_or_update_graph_tool(repo_root=fixture_repo)
    for issue in (f"Fix `{symbol}`.", f"Fix the behavior in {path}."):
        result = service.retrieve_issue_context(
            issue_text=issue, repo_root=fixture_repo, index_file=build.index_file,
        )
        assert result.status is RetrievalStatus.USED
        assert any(item.file_path == path and item.language == language for item in result.items)
    assert symbol in {item.name for item in service.retrieve_issue_context(
        issue_text=f"Fix `{symbol}`.", repo_root=fixture_repo, index_file=build.index_file,
    ).items}


def _retrieve(
    fixture_repo: Path,
    built_index: tuple[RepositoryRetrievalService, Path],
    issue: str,
    *,
    budgets: RetrievalBudgets | None = None,
):
    service, index_file = built_index
    return service.retrieve_issue_context(
        issue_text=issue,
        repo_root=fixture_repo,
        index_file=index_file,
        budgets=budgets,
    )


@pytest.mark.parametrize("max_chars", [500, 1500, 4000, 12000])
def test_compact_budget_retains_explicit_behavior_owner(fixture_repo, built_index, max_chars):
    result = _retrieve(fixture_repo, built_index, "Fix `Worker.run` using `helper`.",
                       budgets=RetrievalBudgets(max_chars=max_chars))
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
    built_index: tuple[RepositoryRetrievalService, Path],
    issue: str,
    expected_path: str,
    expected_reason: str,
) -> None:
    result = _retrieve(fixture_repo, built_index, issue)

    assert result.status is RetrievalStatus.USED
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
    built_index: tuple[RepositoryRetrievalService, Path],
) -> None:
    result = _retrieve(
        fixture_repo,
        built_index,
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
    built_index: tuple[RepositoryRetrievalService, Path],
) -> None:
    _, index_file = built_index
    with GraphStore(index_file, read_only=True) as store:
        lexical_rows, _ = store.search("helper", kind=None, limit=12)
    lexical_names = {str(item["qualified_name"]) for item in lexical_rows}
    result = _retrieve(fixture_repo, built_index, "Fix `helper`.")
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
    built_index: tuple[RepositoryRetrievalService, Path],
) -> None:
    service, index_file = built_index
    (fixture_repo / "other.py").write_text(
        "def helper():\n    return 'other'\n",
        encoding="utf-8",
    )
    commit_all(fixture_repo, "add duplicate helper")
    service.build_or_update_graph_tool(
        repo_root=fixture_repo,
        index_file=index_file,
    )

    result = service.retrieve_issue_context(
        issue_text="Fix `helper` in service.py.",
        repo_root=fixture_repo,
        index_file=index_file,
    )

    matching = [item for item in result.items if item.name == "helper"]
    assert len(matching) >= 2
    assert matching[0].file_path == "service.py"


def test_locked_database_returns_unavailable_within_query_budget(
    fixture_repo: Path,
    built_index: tuple[RepositoryRetrievalService, Path],
    tmp_path: Path,
) -> None:
    service, source = built_index
    index_file = tmp_path / "locked.sqlite3"
    shutil.copy2(source, index_file)
    with sqlite3.connect(index_file) as connection:
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("BEGIN EXCLUSIVE")

        result = service.retrieve_issue_context(
            issue_text="Fix `helper`.",
            repo_root=fixture_repo,
            index_file=index_file,
        )

    assert result.status is RetrievalStatus.UNAVAILABLE
    assert result.duration_ms < 1_000


def test_irrelevant_and_unsupported_language_terms_return_no_match(
    fixture_repo: Path,
    built_index: tuple[RepositoryRetrievalService, Path],
) -> None:
    irrelevant = _retrieve(
        fixture_repo,
        built_index,
        "QuasarNebulaZXQ has unrelated frobnication behavior.",
    )
    unsupported = _retrieve(
        fixture_repo,
        built_index,
        "Update the prose in README.md for lunar deployment.",
    )

    assert irrelevant.status is RetrievalStatus.NO_MATCH
    assert irrelevant.outcome is RetrievalOutcome.NO_LEXICAL_CANDIDATES
    assert irrelevant.items == ()
    assert unsupported.status is RetrievalStatus.NO_MATCH


def test_css_selector_is_available_to_issue_retrieval(tmp_path: Path) -> None:
    with GraphStore(tmp_path / "graph.sqlite3") as store:
        apply_files(store, {
            "index.html": '<button class="checkout-button">Buy</button>',
            "site.css": ".checkout-button { color: blue; }",
        })
        result = retrieve_issue_context(
            "Fix the `.checkout-button` styles.", store,
            index_file=store.path, budgets=RetrievalBudgets(),
        )

    assert result.status is RetrievalStatus.USED
    assert any(item.kind == "Selector" and item.name == ".checkout-button"
               for item in result.items)


def test_candidates_below_threshold_are_distinguished_from_no_candidates(
    fixture_repo: Path,
    built_index: tuple[RepositoryRetrievalService, Path],
) -> None:
    result = _retrieve(
        fixture_repo,
        built_index,
        "Fix `helper`.",
        budgets=RetrievalBudgets(usefulness_threshold=100.0),
    )

    assert result.status is RetrievalStatus.NO_MATCH
    assert result.outcome is RetrievalOutcome.BELOW_THRESHOLD
    assert result.lexical_candidates > 0
    assert result.returned == 0


def test_result_and_character_budgets_truncate_deterministically(
    fixture_repo: Path,
    built_index: tuple[RepositoryRetrievalService, Path],
) -> None:
    budgets = RetrievalBudgets(max_results=1, max_chars=500)
    first = _retrieve(fixture_repo, built_index, "Fix `helper`.", budgets=budgets)
    second = _retrieve(fixture_repo, built_index, "Fix `helper`.", budgets=budgets)

    assert first.status is RetrievalStatus.USED
    assert first.outcome is RetrievalOutcome.USEFUL_CONTEXT_TRUNCATED
    assert first.returned == 1
    assert first.omitted == first.total_candidates - 1
    assert first.context_chars <= 500
    assert [item.qualified_name for item in first.items] == [
        item.qualified_name for item in second.items
    ]


def test_stale_graph_is_unavailable_instead_of_exposing_memory(
    fixture_repo: Path,
    built_index: tuple[RepositoryRetrievalService, Path],
) -> None:
    (fixture_repo / "README.md").write_text("new base\n", encoding="utf-8")
    commit_all(fixture_repo, "advance repository")

    result = _retrieve(fixture_repo, built_index, "Fix `helper`.")

    assert result.status is RetrievalStatus.UNAVAILABLE
    assert result.outcome is RetrievalOutcome.GRAPH_UNAVAILABLE
    assert result.items == ()
    assert result.context == ""


@pytest.mark.parametrize("database_state", ["missing", "corrupt", "unsupported"])
def test_invalid_databases_return_bounded_unavailable_results(
    fixture_repo: Path,
    built_index: tuple[RepositoryRetrievalService, Path],
    tmp_path: Path,
    database_state: str,
) -> None:
    service, source = built_index
    index_file = tmp_path / f"{database_state}.sqlite3"
    if database_state == "corrupt":
        index_file.write_bytes(b"not sqlite")
    elif database_state == "unsupported":
        shutil.copy2(source, index_file)
        with sqlite3.connect(index_file) as connection:
            connection.execute(
                "UPDATE metadata SET value='999' WHERE key='schema_version'"
            )
            connection.commit()

    result = service.retrieve_issue_context(
        issue_text="Fix `helper`.",
        repo_root=fixture_repo,
        index_file=index_file,
    )

    assert result.status is RetrievalStatus.UNAVAILABLE
    assert result.outcome is RetrievalOutcome.GRAPH_UNAVAILABLE
    assert len(result.summary) <= 500
    assert result.returned == 0


def test_foreign_repository_graph_is_not_exposed(
    fixture_repo: Path,
    built_index: tuple[RepositoryRetrievalService, Path],
    tmp_path: Path,
) -> None:
    service, index_file = built_index
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
        index_file=index_file,
    )

    assert result.status is RetrievalStatus.UNAVAILABLE
    assert "different repository" in result.summary


def test_adversarial_issue_text_remains_parameterized_and_bounded(
    fixture_repo: Path,
    built_index: tuple[RepositoryRetrievalService, Path],
) -> None:
    result = _retrieve(
        fixture_repo,
        built_index,
        "`helper' OR 1=1; DROP TABLE nodes; --` ../../service.py \x1b[31m",
    )
    service, index_file = built_index
    stats = service.graph_stats(repo_root=fixture_repo, index_file=index_file)

    assert result.status in {RetrievalStatus.USED, RetrievalStatus.NO_MATCH}
    assert stats.nodes > 0
    assert len(result.context) <= 12_000


def test_invalid_node_paths_are_never_returned(
    fixture_repo: Path,
    built_index: tuple[RepositoryRetrievalService, Path],
) -> None:
    service, index_file = built_index
    with sqlite3.connect(index_file) as connection:
        connection.execute(
            "UPDATE nodes SET file_path='../escape.py' WHERE name='helper'"
        )
        connection.commit()

    result = service.retrieve_issue_context(
        issue_text="Fix `helper`.",
        repo_root=fixture_repo,
        index_file=index_file,
    )

    assert all(".." not in Path(item.file_path).parts for item in result.items)


def test_one_failed_expansion_preserves_primary_hits(
    fixture_repo: Path,
    built_index: tuple[RepositoryRetrievalService, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sage.harness.retrieval.ranking as retrieval

    def fail_expansion(*_args, **_kwargs):
        raise sqlite3.OperationalError("synthetic expansion failure")

    monkeypatch.setattr(retrieval, "_expand_edges", fail_expansion)
    result = _retrieve(fixture_repo, built_index, "Fix `helper`.")

    assert result.status is RetrievalStatus.USED
    assert any(item.name == "helper" for item in result.items)
    assert result.warnings


def test_explicit_symbol_outranks_unrelated_configuration(tmp_path: Path):
    with GraphStore(tmp_path / "graph.sqlite3") as store:
        apply_files(store, {"service.py": "class WebhookService:\n    def process(self):\n        pass\n",
                      "config.py": "def settings():\n    return 1\n"})
        result = retrieve_issue_context("Fix `WebhookService.process` retry handling.", store,
            index_file=store.path, budgets=RetrievalBudgets())
        assert result.items[0].qualified_name == "service.py::WebhookService.process"
        assert "semantic" not in result.search_modes
        assert any(d.channel_ranks.get("lexical") for d in result.diagnostics)
        assert result.duration_ms >= 0


def test_unresolved_edge_is_not_rebound_to_a_test_double(tmp_path: Path):
    with GraphStore(tmp_path / "graph.sqlite3") as store:
        apply_files(store, {"repo.py": "class Repo:\n    def claim(self):\n        self.collection.insert_one({})\n",
                      "tests/fakes.py": "class FakeCollection:\n    def insert_one(self, data):\n        pass\n"})
        assert store.node("insert_one") is not None
        assert store.exact_node("insert_one") is None
        result = retrieve_issue_context("Fix `Repo.claim`.", store,
            index_file=store.path, budgets=RetrievalBudgets())
        assert not any(i.name == "insert_one" and any(r.relationship == "CALLS" for r in i.relationships)
                       for i in result.items)
        assert result.unresolved_edges > 0
