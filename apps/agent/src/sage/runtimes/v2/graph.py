"""Deterministic candidate helpers for the sequential Sage V2 workflow."""

from __future__ import annotations

import hashlib
import json

from sage.domain.review import ReviewResult
from sage.domain.solver import CandidateSnapshot, SavedSolverPlan, SolverFinalResult
from sage.errors import AgentRuntimeError
from sage.repository import RepositoryTools

GRAPH_NAME = "sage_v2_tool_driven"


def create_candidate_snapshot(
    *,
    repository: RepositoryTools,
    base_sha: str,
    plan: SavedSolverPlan,
    solver_result: SolverFinalResult,
    max_diff_chars: int,
) -> CandidateSnapshot:
    """Derive the authoritative candidate entirely from current Git state."""

    if repository.get_head_sha() != base_sha:
        raise AgentRuntimeError("Candidate HEAD no longer matches the accepted base SHA.")
    diff = repository.get_complete_diff()
    changed_files = tuple(repository.get_changed_files())
    if not diff.strip() or not changed_files:
        raise AgentRuntimeError(
            "Implemented Solver result did not produce an authoritative candidate."
        )
    if len(diff) > max_diff_chars:
        raise AgentRuntimeError("Candidate diff exceeds the configured V2 context cap.")
    return CandidateSnapshot(
        base_sha=base_sha,
        changed_files=changed_files,
        diff=diff,
        diff_digest=hashlib.sha256(diff.encode("utf-8")).hexdigest(),
        plan_version=plan.version,
        plan_digest=plan.digest,
        solver_summary=solver_result.summary,
        verification_claims=solver_result.verification_claims,
        remaining_uncertainty=solver_result.remaining_uncertainty,
    )
def review_fingerprint(review: ReviewResult) -> str:
    """Return a stable no-progress fingerprint for blocking findings."""

    payload = [
        {
            "criteria": sorted(finding.criterion_ids),
            "evidence": " ".join(finding.evidence.lower().split()),
            "required": " ".join(finding.required_outcome.lower().split()),
            "path": finding.path,
            "line": finding.line,
        }
        for finding in review.blocking_findings
    ]
    rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()
