"""Bounded histories preserve primary evidence and restart optional visibility."""

from types import SimpleNamespace

import pytest

from sage.errors import AgentRuntimeError
from sage.harness.context.packets import build_review_message, prepare_solver_message


def test_repair_resets_visibility_and_adds_only_the_fresh_history_packet():
    calls = []
    memory = SimpleNamespace(begin_session=lambda **kw: calls.append(kw) or "Unchanged source locator")
    navigation = SimpleNamespace(begin_session=lambda **kw: calls.append(kw))
    context = SimpleNamespace(memory=memory, navigation=navigation,
                              settings=SimpleNamespace(solver_input_chars=1000, repair_input_chars=200))
    assert prepare_solver_message("Initial packet", stage="solver", context=context) == "Initial packet"
    repaired = prepare_solver_message("Repair evidence", stage="solver-repair", context=context)
    assert repaired.startswith("Repair evidence")
    assert repaired.count("Unchanged source locator") == 1
    assert calls == [{"stage": "solver"}, {"initial_visible": True},
                     {"stage": "solver-repair"}, {"initial_visible": False}]


def test_solver_cap_checks_context_after_repair_enrichment():
    context = SimpleNamespace(memory=SimpleNamespace(begin_session=lambda **kw: "x" * 100),
        navigation=None, settings=SimpleNamespace(solver_input_chars=200, repair_input_chars=100))
    with pytest.raises(AgentRuntimeError, match="safe input cap"):
        prepare_solver_message("Repair evidence", stage="solver-repair", context=context)


def test_review_packet_is_bounded_before_model_invocation():
    fields = dict(issue_text="Issue", plan_json="{}", changed_files_json="[]",
                  candidate_diff="diff", verification_json="{}", solver_summary="summary")
    packet = build_review_message(**fields, max_chars=1000)
    assert "<actual-git-diff>\ndiff" in packet
    with pytest.raises(AgentRuntimeError, match="Reviewer context"):
        build_review_message(**fields, max_chars=len(packet) - 1)
