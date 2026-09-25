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
    for arm, duration, cache in [("off", 100, "warm"), ("on", 80, "warm"), ("shadow", 50, "cold")]:
        root = tmp_path / arm
        root.mkdir()
        (root / "usage.json").write_text(json.dumps({"calls": [], "semantic_calls": []}))
        (root / "workflow-timing.json").write_text(json.dumps({"duration_ms": duration}))
        (root / "agent-final.json").write_text(json.dumps({"outcome": "completed"}))
        rows.append(dict(case_id="issue", base_sha="sha", split="held-out", repeat=1,
            settings_id="models-v1", cache_state=cache, arm=arm, run_dir=str(root), independent_quality_pass=True))
    result = evaluator.compare({"runs": rows})
    assert result["paired_deltas"]["on"]["wall_ms"]["mean"] == -20
    assert result["paired_deltas"]["shadow"]["wall_ms"]["n"] == 0
    assert result["paired_deltas"]["on"]["wall_ms"]["mean_ci95"] is None


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
    from sage.harness.jev.provider import build_request, SCORE_LEVELS
    from sage.domain.relevance import FileCandidate
    candidates = (FileCandidate(id="f0", path="app.py", evidence="source hit"),)
    capture = {"request": build_request("jev-1.13.0", {"issue": "Fix source"}, candidates),
        "response": {"model": "jev-1.13.0", "usage": {"input_tokens": 10, "output_tokens": 5},
            "answers": {"f0": {"type": "score", "score": 3, "confidence": .9,
                "legend": dict(enumerate(SCORE_LEVELS)),
                "probabilities": {"0": 0., "1": 0., "2": 0., "3": 1.}}}}}
    path = tmp_path / "relevance.json"
    artifact = {"policy": "file-relevance-v1", "score_threshold": 2, "confidence_threshold": .5, "capture": capture}
    path.write_text(json.dumps(artifact))
    assert evaluator.replay(path)["retained_files"] == ["app.py"]
    artifact["confidence_threshold"] = 1
    path.write_text(json.dumps(artifact))
    assert evaluator.replay(path)["retained_files"] == []
