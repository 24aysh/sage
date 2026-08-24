from __future__ import annotations

from typing import Any, cast

from sage.config import Settings
from sage.runtimes.v2.graph import (
    V2Services,
    _prior_clarification_round,
    _verification_log_detail,
    build_graph,
)


def test_v2_graph_is_sequential_and_renders_mermaid(tmp_path) -> None:
    dependency = cast(Any, object())
    graph = build_graph(
        V2Services(
            settings=Settings(
                runtime="v2-prototype",
                openai_api_key="openai-test",
                gemini_api_key="gemini-test",
                google_model_context_approved=True,
            ),
            repository=dependency,
            scout=dependency,
            compiler=dependency,
            calls=dependency,
            verifier=dependency,
            artifacts=dependency,
            base_sha="a" * 40,
            workspace=tmp_path,
        )
    )

    rendered = graph.get_graph().draw_mermaid()
    print(rendered)

    assert "intake_planner" in rendered
    assert "solver" in rendered
    assert "reviewer" in rendered
    assert rendered.index("intake_planner") < rendered.index("solver")
    forbidden = ("worker", "dispatch", "merge_agent", "replan", "Send")
    assert all(name not in rendered for name in forbidden)


def test_prior_clarification_round_accepts_rendered_controller_marker() -> None:
    issue_text = (
        "Prior status:\n"
        "&lt;!-- sage-clarification:v1 round=2 "
        "disposition=needs_human_information -->"
    )

    assert _prior_clarification_round(issue_text) == 2


def test_verification_log_detail_is_concise_and_single_line() -> None:
    output = (
        "STDOUT\n"
        "test_calculator.py:10: new blank line at EOF.\n\n"
        "STDERR\n"
        "one more detail\n"
    )

    assert _verification_log_detail(output) == (
        "test_calculator.py:10: new blank line at EOF. one more detail"
    )
