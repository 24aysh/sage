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

from sage.domain.relevance import FileCandidate
from sage.harness.jev.provider import parse_response


async def solve_arm(args) -> dict:
    from sage.composition import build_orchestrator, build_retrieval_service
    from sage.config import Settings, JevSettings
    from sage.domain.solve import SolveRequest
    from sage.workflows.solve import solve_issue

    if not args.allow_paid_solve:
        raise ValueError("run requires --allow-paid-solve; Solver/Reviewer still incur cost in every arm")
    settings = Settings.from_env()
    jev = settings.jev.model_copy(update={"mode": args.arm})
    if args.arm != "off" and not jev.api_key:
        raise ValueError("Enabled relevance filtering requires TYPESAFE_API_KEY")
    settings = settings.model_copy(update={"jev": jev})
    orchestrator = build_orchestrator(settings)
    request = SolveRequest(repo_path=args.repo, issue_path=args.issue_file, base_ref=args.base_ref,
                           index_file=args.index_file)
    retrieval = build_retrieval_service() if args.index_file else None
    result = await solve_issue(request, orchestrator, settings, retrieval_service=retrieval)
    return {"arm": args.arm, "run_dir": str(result.run_dir), "outcome": result.outcome.value,
            "base_sha": result.base_sha}


def replay(path: Path, labels: dict | None = None) -> dict:
    artifact = json.loads(path.read_text())
    if artifact.get("policy") != "file-relevance-v1":
        raise ValueError("Legacy navigation captures require the historical evaluator")
    capture = artifact.get("capture") or {}
    if "response" not in capture:
        return {"kind": "offline_relevance_proxy", "retained_files": [], "note": "No captured response"}
    request = capture["request"]
    candidates = tuple(FileCandidate(id=key, path=q["instructions"]["file"],
        evidence=q["instructions"]["evidence"]) for key, q in request["questions"].items())
    decision = parse_response(capture["response"], model=request["model"], candidates=candidates)
    accepted = [c.path for c in candidates if decision.scores[c.id] >= artifact["score_threshold"]
                and decision.confidences[c.id] >= artifact["confidence_threshold"]]
    return {"kind": "offline_relevance_proxy", "retained_files": accepted,
            "rejected_files": [c.path for c in candidates if c.path not in accepted],
            "labeled_relevant_retained": sum(bool((labels or {}).get(path)) for path in accepted),
            "note": "Selection replay does not establish token, cost, or latency savings."}


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
    known = [c["input_tokens"] for c in calls if c.get("input_tokens") is not None]
    unknown = sum(c.get("input_tokens") is None or c.get("output_tokens") is None for c in calls)
    if (root / "navigation.json").exists():
        raise ValueError("Legacy navigation runs cannot be compared as relevance-filter runs")
    filter_path = root / "relevance-filter.json"
    relevance = json.loads(filter_path.read_text()) if filter_path.exists() else {}
    return {"wall_ms": timing["duration_ms"], "input_tokens": sum(known) if not unknown else None,
        "known_input_tokens": sum(known), "unknown_usage_calls": unknown, "cost_usd": _cost(calls, prices),
        "solver_calls": sum(c.get("role") == "solver" for c in usage.get("calls", [])),
        "review_calls": sum(c.get("role") == "reviewer" for c in usage.get("calls", [])),
        "semantic_calls": len(usage.get("semantic_calls", [])),
        "discarded_items": relevance.get("discarded_items", 0),
        "retained_files": len(relevance.get("retained_files", [])),
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
               "discarded_items", "retained_files", "model_sessions", "quality_pass")
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
    replay_parser.add_argument("--labels", type=Path, help='Optional JSON mapping file paths to relevance labels')
    compare_parser = commands.add_parser("compare")
    compare_parser.add_argument("manifest", type=Path)
    run_parser = commands.add_parser("run", help="Explicit paid local solve; requires Docker and configured credentials")
    run_parser.add_argument("--arm", required=True, choices=["off", "shadow", "on"])
    run_parser.add_argument("--repo", type=Path, required=True)
    run_parser.add_argument("--issue-file", type=Path, required=True)
    run_parser.add_argument("--base-ref", required=True, help="Use a fixed base SHA for every paired arm")
    run_parser.add_argument("--index-file", "--memory-file", dest="index_file", type=Path)
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
