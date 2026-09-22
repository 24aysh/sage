"""Capture replay and paired-run comparison; only explicit `run` makes paid calls.

Run with the agent environment: python apps/agent/evals/navigation.py --help.
Prices are supplied by the evaluator; unknown usage/cost is never treated as free.
"""

import argparse
import asyncio
import json
import math
import random
import statistics
from pathlib import Path

from sage.domain.navigation import ActionCandidate, NavigationDecision
from sage.orchestration.navigation_candidates import (DEFAULT_ACTION_CONFIDENCE_THRESHOLD,
    DEFAULT_ACTION_PROBABILITY_THRESHOLDS, accept_action, excerpt_selection)
from sage.providers.typesafe import parse_response


class DeterministicSelector:
    """Evaluation-only stable-first excerpts or objective-bearing zero-action control."""
    model = "deterministic-control"
    capture = None

    async def rank_excerpts(self, *, state, candidates, timeout):
        return NavigationDecision(model=self.model, scores={c.id: 3 for c in candidates[:2]},
            confidences={c.id: 1 for c in candidates[:2]}, input_tokens=0, output_tokens=0)

    async def choose_action(self, **kwargs):
        return NavigationDecision(model=self.model, input_tokens=0, output_tokens=0)

    async def aclose(self):
        pass


async def solve_arm(args) -> dict:
    from sage.composition import build_orchestrator, build_legion_memory_service
    from sage.config import Settings, JevSettings, LegionEmbeddingSettings
    from sage.domain.solve import SolveRequest
    from sage.orchestration.navigation import NavigationSession
    from sage.workflows.solve import solve_issue

    if not args.allow_paid_solve:
        raise ValueError("run requires --allow-paid-solve; Solver/Reviewer still incur cost in every arm")
    settings = Settings.from_env()
    deterministic = args.arm in {"deterministic-excerpts", "actions-0"}
    jev = JevSettings(mode="off") if args.arm == "off" else JevSettings(
        mode="on", policy="actions" if args.arm.startswith("actions-") else "excerpts",
        max_followup_actions=2 if args.arm == "actions-2" else 1,
        api_key="evaluation-control" if deterministic else settings.jev.api_key,
        model=settings.jev.model, capture=settings.jev.capture,
        read_probability_threshold=settings.jev.read_probability_threshold,
        search_probability_threshold=settings.jev.search_probability_threshold,
        graph_probability_threshold=settings.jev.graph_probability_threshold,
        action_confidence_threshold=settings.jev.action_confidence_threshold)
    settings = settings.model_copy(update={"jev": jev})
    orchestrator = build_orchestrator(settings)
    if deterministic:
        # Use the same production controller, resource lifecycle and caps, with an injected judgment boundary.
        orchestrator._navigation_factory = lambda **kw: NavigationSession(provider=DeterministicSelector(), **kw)
    request = SolveRequest(repo_path=args.repo, issue_path=args.issue_file, base_ref=args.base_ref,
                           memory_file=args.memory_file)
    memory = build_legion_memory_service(embeddings=LegionEmbeddingSettings.from_env()) if args.memory_file else None
    result = await solve_issue(request, orchestrator, settings, memory_service=memory)
    return {"arm": args.arm, "run_dir": str(result.run_dir), "outcome": result.outcome.value,
            "base_sha": result.base_sha}


def replay(path: Path, labels: dict | None = None) -> dict:
    artifact = json.loads(path.read_text())
    probability_thresholds = artifact.get("action_probability_thresholds", DEFAULT_ACTION_PROBABILITY_THRESHOLDS)
    confidence_threshold = artifact.get("action_confidence_threshold", DEFAULT_ACTION_CONFIDENCE_THRESHOLD)
    cases = []
    for record in artifact["records"]:
        capture = record.get("capture")
        if not capture or "response" not in capture:
            continue
        candidates = tuple(ActionCandidate.model_validate(c) for c in capture["candidates"])
        request = capture["request"]
        actions = artifact["policy"] == "actions"
        decision = parse_response(capture["response"], model=request["model"], candidates=candidates, actions=actions)
        selected = decision.selected if actions else excerpt_selection(decision)
        if actions and selected:
            candidate = next(c for c in candidates if c.id == selected[0])
            probability_threshold = probability_thresholds.get(candidate.action.kind,
                DEFAULT_ACTION_PROBABILITY_THRESHOLDS[candidate.action.kind])
            if not accept_action(decision, candidate, probability_threshold=probability_threshold,
                                 confidence_threshold=confidence_threshold):
                selected = ()
        key = f"{record['sequence']}:{record['step']}"
        relevant = set((labels or {}).get(key, []))
        available = {c.id for c in candidates}
        # Stable-first baseline has the same candidate and exposure-count caps.
        baseline = tuple(c.id for c in candidates[:1 if actions else 2])
        cases.append({"sequence_step": key, "candidate_count": len(candidates),
            "jev_selected": selected, "deterministic_selected": baseline,
            "labeled": key in (labels or {}), "candidate_coverage": bool(available & relevant),
            "jev_relevant_selected": len(set(selected) & relevant),
            "deterministic_relevant_selected": len(set(baseline) & relevant)})
    return {"kind": "offline_selection_proxy", "cases": cases,
        "note": "Captured dependent steps are replayed, not counterfactual executions. "
                "Selection agreement is not token, cost, or latency savings."}


def _cost(calls: list[dict], prices: dict) -> float | None:
    total = 0.0
    for call in calls:
        if call["model"] == "deterministic-control" and call.get("input_tokens") == call.get("output_tokens") == 0:
            continue
        price = prices.get(call["model"])
        incoming, outgoing = call.get("input_tokens"), call.get("output_tokens")
        if price is None or incoming is None or outgoing is None:
            return None
        cached = call.get("cached_tokens") or 0
        if not 0 <= cached <= incoming:
            raise ValueError("Invalid cached-token accounting")
        total += ((incoming - cached) * price["input_per_million"] + outgoing * price["output_per_million"]
                  + cached * price.get("cached_input_per_million", price["input_per_million"])) / 1e6
    return total


def run_metrics(row: dict, prices: dict) -> dict:
    root = Path(row["run_dir"])
    usage = json.loads((root / "usage.json").read_text())
    final = json.loads((root / "agent-final.json").read_text())
    timing = json.loads((root / "workflow-timing.json").read_text())
    calls = [*usage.get("calls", []), *usage.get("semantic_calls", [])]
    memory_path = root / "legion-memory.json"
    memory = json.loads(memory_path.read_text()) if memory_path.exists() else {}
    embedding = memory.get("embedding_usage") or {}
    if embedding.get("document_calls", 0) + embedding.get("query_calls", 0):
        vectors = (memory.get("build") or {}).get("vectors") or {}
        calls.append({"model": vectors.get("model", "unknown-embedding"),
                      "input_tokens": embedding.get("input_tokens"), "output_tokens": 0})
    known = [c["input_tokens"] for c in calls if c.get("input_tokens") is not None]
    unknown = sum(c.get("input_tokens") is None or c.get("output_tokens") is None for c in calls)
    navigation_path = root / "navigation.json"
    navigation = json.loads(navigation_path.read_text()) if navigation_path.exists() else {}
    return {"wall_ms": timing["duration_ms"], "input_tokens": sum(known) if not unknown else None,
        "known_input_tokens": sum(known), "unknown_usage_calls": unknown, "cost_usd": _cost(calls, prices),
        "solver_calls": sum(c.get("role") == "solver" for c in usage.get("calls", [])),
        "review_calls": sum(c.get("role") == "reviewer" for c in usage.get("calls", [])),
        "semantic_calls": len(usage.get("semantic_calls", [])),
        "internal_operations": navigation.get("operations", 0), "added_chars": navigation.get("added_chars", 0),
        "quality_pass": final["outcome"] == "completed" and row["independent_quality_pass"],
        "model_sessions": usage.get("solver_sessions", 0)}


def _summary(values: list[float]) -> dict:
    if not values:
        return {"n": 0, "mean": None, "median": None, "p95": None, "mean_ci95": None}
    rng = random.Random(0)
    means = sorted(statistics.mean(rng.choices(values, k=len(values))) for _ in range(2000))
    return {"n": len(values), "mean": statistics.mean(values), "median": statistics.median(values),
        "p95": sorted(values)[math.ceil(.95 * len(values)) - 1],
        "mean_ci95": [means[50], means[1949]] if len(values) >= 2 else None}


def compare(manifest: dict) -> dict:
    """Pair repeats by fixed Issue/base/config/cache identity, never just by arm name."""
    runs = {}
    for row in manifest["runs"]:
        if type(row["independent_quality_pass"]) is not bool:
            raise ValueError("independent_quality_pass must be an independently assessed boolean")
        key = tuple(row[k] for k in ("case_id", "base_sha", "split", "repeat", "settings_id", "cache_state"))
        arm = row["arm"]
        if arm in runs.setdefault(key, {}):
            raise ValueError("Duplicate paired run")
        runs[key][arm] = run_metrics(row, manifest.get("prices", {}))
    arms = sorted({arm for group in runs.values() for arm in group})
    baseline = manifest.get("baseline", "off")
    metrics = ("wall_ms", "input_tokens", "cost_usd", "solver_calls", "review_calls", "semantic_calls",
               "internal_operations", "added_chars", "model_sessions", "quality_pass")
    summary, pairs = {}, {}
    for arm in arms:
        values = [group[arm] for group in runs.values() if arm in group]
        summary[arm] = {metric: _summary([v[metric] for v in values if v[metric] is not None]) for metric in metrics}
        summary[arm]["unknown_usage_calls"] = sum(v["unknown_usage_calls"] for v in values)
        if arm != baseline:
            comparable = [group for group in runs.values() if arm in group and baseline in group]
            pairs[arm] = {metric: _summary([g[arm][metric] - g[baseline][metric] for g in comparable
                if g[arm][metric] is not None and g[baseline][metric] is not None]) for metric in metrics}
    return {"arms": summary, "paired_deltas": pairs, "baseline": baseline,
        "note": "Negative deltas reduce resource use; quality_pass must not regress. "
        "Small-sample intervals are descriptive, not promotion evidence. Keep tuning and held-out reports separate."}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    replay_parser = commands.add_parser("replay")
    replay_parser.add_argument("capture", type=Path)
    replay_parser.add_argument("--labels", type=Path, help='Optional JSON mapping "sequence:step" to relevant candidate IDs')
    compare_parser = commands.add_parser("compare")
    compare_parser.add_argument("manifest", type=Path)
    run_parser = commands.add_parser("run", help="Explicit paid local solve; requires Docker and configured credentials")
    run_parser.add_argument("--arm", required=True, choices=["off", "deterministic-excerpts", "jev-excerpts",
                                                          "actions-0", "actions-1", "actions-2"])
    run_parser.add_argument("--repo", type=Path, required=True)
    run_parser.add_argument("--issue-file", type=Path, required=True)
    run_parser.add_argument("--base-ref", required=True, help="Use a fixed base SHA for every paired arm")
    run_parser.add_argument("--memory-file", type=Path)
    run_parser.add_argument("--allow-paid-solve", action="store_true")
    args = parser.parse_args()
    if args.command == "replay":
        result = replay(args.capture, json.loads(args.labels.read_text()) if args.labels else None)
    elif args.command == "compare":
        result = compare(json.loads(args.manifest.read_text()))
    else:
        result = asyncio.run(solve_arm(args))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
