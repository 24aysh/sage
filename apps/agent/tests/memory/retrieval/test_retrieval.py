from sage.memory.models import NodeType, RetrievalCandidate, SearchDocument
from sage.memory.retrieval.beam import navigate
from sage.memory.retrieval.exact import exact_candidates
from sage.memory.retrieval.sparse import SQLiteSparseIndex


def test_exact_evidence_outranks_lexical_evidence() -> None:
    documents = [
        SearchDocument(
            path="src/sage/memory.py",
            node_type=NodeType.FILE,
            symbols=("MemoryEngine",),
        ),
        SearchDocument(path="tests/test_memory.py", node_type=NodeType.FILE),
    ]

    candidates = exact_candidates(
        "Fix MemoryEngine in src/sage/memory.py", documents
    )

    assert candidates[0].path == "src/sage/memory.py"
    assert candidates[0].evidence_tier == "exact_path"


def test_sqlite_fts_escapes_query_syntax_and_excludes_negative_scope() -> None:
    index = SQLiteSparseIndex()
    index.rebuild(
        [
            SearchDocument(
                path="src/memory.py",
                node_type=NodeType.FILE,
                summary="Persists repository context",
                responsibilities=("snapshot storage",),
                concepts=("memory",),
            )
        ]
    )
    try:
        assert index.search('memory OR "unterminated', limit=5)[0][0] == "src/memory.py"
        assert index.search("definitely-absent", limit=5) == []
    finally:
        index.close()


def test_beam_keeps_independent_ancestry_and_stable_ties() -> None:
    candidates = [
        RetrievalCandidate(
            path=f"{root}/{name}.py",
            node_type=NodeType.FILE,
            score=score,
            evidence_tier="test",
            ancestry=root,
            reason="test",
        )
        for root, name, score in (
            ("src", "a", 10),
            ("src", "b", 9),
            ("tests", "a", 8),
            ("docs", "a", 7),
        )
    ]

    selected, rounds = navigate(
        candidates, beam_width=3, max_rounds=2, max_files=3
    )

    assert {item.ancestry for item in selected} == {"src", "tests", "docs"}
    assert rounds == 1
