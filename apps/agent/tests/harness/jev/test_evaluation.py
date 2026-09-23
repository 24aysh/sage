"""Offline evaluation does not infer savings from selector agreement."""

import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def evaluator():
    path = Path(__file__).parents[3] / "evals/navigation.py"
    spec = importlib.util.spec_from_file_location("navigation_evaluation", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cost_keeps_unknowns_and_includes_cached_and_semantic_usage(evaluator):
    prices = {"solver": {"input_per_million": 10, "cached_input_per_million": 1, "output_per_million": 20},
              "jev": {"input_per_million": 1, "output_per_million": 0}}
    calls = [{"model": "solver", "input_tokens": 100, "cached_tokens": 50, "output_tokens": 10},
             {"model": "jev", "input_tokens": 100, "output_tokens": 10}]
    assert evaluator._cost(calls, prices) == pytest.approx(.00085)
    calls.append({"model": "jev", "input_tokens": None, "output_tokens": None})
    assert evaluator._cost(calls, prices) is None


def test_pairing_requires_matching_case_base_settings_and_cache(evaluator, tmp_path):
    rows = []
    for arm, duration, cache in [("off", 100, "warm"), ("actions-1", 80, "warm"), ("actions-2", 50, "cold")]:
        root = tmp_path / arm
        root.mkdir()
        (root / "usage.json").write_text(json.dumps({"calls": [], "semantic_calls": []}))
        (root / "workflow-timing.json").write_text(json.dumps({"duration_ms": duration}))
        (root / "agent-final.json").write_text(json.dumps({"outcome": "completed"}))
        rows.append(dict(case_id="issue", base_sha="sha", split="held-out", repeat=1,
            settings_id="models-v1", cache_state=cache, arm=arm, run_dir=str(root), independent_quality_pass=True))
    result = evaluator.compare({"runs": rows})
    assert result["paired_deltas"]["actions-1"]["wall_ms"]["mean"] == -20
    assert result["paired_deltas"]["actions-2"]["wall_ms"]["n"] == 0
    assert result["paired_deltas"]["actions-1"]["wall_ms"]["mean_ci95"] is None


def test_live_arm_requires_explicit_paid_acknowledgment(evaluator):
    import asyncio
    from types import SimpleNamespace
    with pytest.raises(ValueError, match="allow-paid-solve"):
        asyncio.run(evaluator.solve_arm(SimpleNamespace(allow_paid_solve=False)))


def test_legacy_embedding_costs_are_not_silently_omitted(evaluator, tmp_path):
    (tmp_path / "usage.json").write_text('{"calls": [], "semantic_calls": []}')
    (tmp_path / "agent-final.json").write_text('{"outcome": "completed"}')
    (tmp_path / "workflow-timing.json").write_text('{"duration_ms": 100}')
    (tmp_path / "legion-memory.json").write_text('{"embedding_usage": {"document_calls": 2}}')
    with pytest.raises(ValueError, match="historical evaluator"):
        evaluator.run_metrics({"run_dir": str(tmp_path)}, {})


def test_exact_capture_replays_without_provider(evaluator, tmp_path):
    from sage.harness.jev.provider import build_request
    from sage.domain.navigation import ActionCandidate, ReadAction
    candidates = (ActionCandidate(id="c0", action=ReadAction(path="app.py", start_line=1, end_line=40),
                                  evidence="source hit"),)
    capture = {"request": build_request("jev-1.13.0", {"goal": "read implementation"}, candidates, actions=True),
        "candidates": [c.model_dump() for c in candidates],
        "response": {"model": "jev-1.13.0", "usage": {"input_tokens": 10, "output_tokens": 5},
                     "answers": {"next": {"type": "choice", "choice": "c0", "confidence": .9,
                                           "probabilities": {"c0": .95, "RETURN_TO_SOLVER": .05}}}}}
    path = tmp_path / "navigation.json"
    path.write_text(json.dumps({"policy": "actions", "records": [{"sequence": 1, "step": 1, "capture": capture}]}))
    result = evaluator.replay(path, {"1:1": ["c0"]})
    assert result["cases"][0]["candidate_coverage"]
    assert result["cases"][0]["jev_selected"] == ("c0",)
    assert result["kind"] == "offline_selection_proxy"

    path.write_text(json.dumps({"policy": "actions", "action_probability_thresholds": {"read_file": .96},
        "action_confidence_threshold": .5, "records": [{"sequence": 1, "step": 1, "capture": capture}]}))
    assert evaluator.replay(path)["cases"][0]["jev_selected"] == ()
