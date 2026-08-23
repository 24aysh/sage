from __future__ import annotations

import hashlib
from typing import Any, cast

import pytest

from sage.config import Settings
from sage.context.compiler import ContextBudgetError, ContextCompiler
from sage.repository.scout import RepositoryExcerpt, RepositoryMap


def _map(*, excerpt: str = "") -> RepositoryMap:
    return RepositoryMap(
        base_sha="a" * 40,
        tracked_file_count=1,
        tracked_paths_sample=("app.py",),
        top_level_summary=("app.py",),
        language_summary={"python": 1},
        manifests=(),
        test_roots=(),
        ci_build_files=(),
        documentation_files=(),
        likely_entry_points=("app.py",),
        exact_issue_paths=("app.py",),
        lexical_matches=(),
        key_excerpts=(
            (RepositoryExcerpt(path="app.py", content=excerpt),) if excerpt else ()
        ),
    )


def test_planner_packet_is_bounded_delimited_and_digest_addressed() -> None:
    compiler = ContextCompiler(
        repository=cast(Any, object()),
        settings=Settings(
            openai_api_key="test",
            planner_input_chars=4_000,
        ),
    )

    packet = compiler.compile_intake(
        issue_text="Change app.py. <!-- pretend controller instruction -->",
        repository_map=_map(excerpt="x" * 12_000),
        clarification_round=0,
    )

    assert packet.character_count == len(packet.content) <= 4_000
    assert "BEGIN task (UNTRUSTED DATA" in packet.content
    assert packet.omitted_sections == ("repository_excerpt:app.py",)
    assert packet.digest == hashlib.sha256(packet.content.encode()).hexdigest()


def test_mandatory_context_that_exceeds_cap_fails_without_truncation() -> None:
    compiler = ContextCompiler(
        repository=cast(Any, object()),
        settings=Settings(openai_api_key="test", planner_input_chars=4_000),
    )

    with pytest.raises(ContextBudgetError, match="Mandatory planner context"):
        compiler.compile_intake(
            issue_text="x" * 10_000,
            repository_map=_map(),
            clarification_round=0,
        )
