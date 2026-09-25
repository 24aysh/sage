"""Stable terminal rendering shared by solve and memory commands."""

from __future__ import annotations

import json
from pathlib import Path

from sage.domain.memory import MemoryRetrievalResult, MemoryRetrievalStatus
from sage.domain.solve import SolveOutcome, SolveResult


def _render_result(result: SolveResult, *, model: str) -> None:
    print("Sage")
    print()
    print(f"Run: {result.run_id}")
    print(f"Base: {result.base_sha[:12]}")
    print(f"Model: {model}")
    print()
    if result.outcome is SolveOutcome.INTERRUPTED:
        print("Solve interrupted — partial usage and elapsed time at cancellation.")
        print("Candidate changes are unverified; this is not a completed solve.")
        print(f"Run artifacts: {result.run_dir}")
        print(f"Workspace: {result.workspace_dir}")
    elif result.changed_files:
        print("Changed files:")
        for path in result.changed_files:
            print(f"  {path}")
        print()
        print("Summary:")
        print(f"  {result.summary}")
        print()
        print("Workspace:")
        print(f"  {result.workspace_dir}")
        print()
        print("Patch:")
        print(f"  {result.run_dir / 'diff.patch'}")
    else:
        print("Agent completed without producing a repository change.")
        print()
        print("Summary:")
        print(f"  {result.summary}")
        if result.remaining_uncertainty:
            print()
            print("Remaining uncertainty:")
            for uncertainty in result.remaining_uncertainty:
                print(f"  {uncertainty}")
        print()
        print("Run artifacts:")
        print(f"  {result.run_dir}")

    _render_solve_memory_summary(result)
    _render_solve_usage_summary(result)
    _render_solve_timing_summary(result)
    if result.outcome is SolveOutcome.INTERRUPTED:
        print("Cleanup continues; unreported in-flight tokens may still be billed.", flush=True)


def _render_solve_timing_summary(result: SolveResult) -> None:
    print()
    print("Elapsed time totals:")
    provenance = result.provenance
    solver = reviewer = jev = without_jev = None
    if provenance is not None and provenance.agent_timings is not None:
        solver = sum(t.duration_ms for t in provenance.agent_timings if t.role == "solver")
        reviewer = sum(t.duration_ms for t in provenance.agent_timings if t.role == "reviewer")
        jev = sum(call.latency_ms for call in provenance.semantic_calls)
        without_jev = max(0.0, solver - jev)
    for label, elapsed in (("Solver (including Jev)", solver), ("Solver (without Jev)", without_jev),
                           ("Jev (decisions only)", jev), ("Reviewer", reviewer)):
        value = f"{elapsed / 1000:.2f} seconds" if elapsed is not None else "unavailable"
        print(f"  {label}: {value}")
    duration = result.workflow_duration_ms
    label = "Elapsed time at interruption" if result.outcome is SolveOutcome.INTERRUPTED else "Total solve time"
    print(f"{label}: {duration / 1000:.2f} seconds" if duration is not None else f"{label}: unavailable")


def _render_solve_memory_summary(result: SolveResult) -> None:
    memory = result.memory
    if memory is None:
        return
    print()
    print("Legion Memory:")
    print(f"  Status: {memory.status.value}")
    print(
        "  Initial retrieval: "
        f"{memory.retrieval.returned if memory.retrieval is not None else 0} memories"
    )
    print(f"  Native memory tool calls: {len(memory.tool_calls)}")
    exposure = memory.exposure
    print("  Exposure: " + ", ".join(f"{name}={'yes' if getattr(exposure, name) else 'no'}"
          for name in ("available", "retrieved", "exposed", "queried", "read_enriched")))
    print(f"  Memory characters (across {exposure.sessions} histories): initial={exposure.initial_context_chars}, "
          f"enrichment={exposure.enrichment_chars}, graph responses={exposure.graph_response_chars}")
    print(f"  Source-read characters: {exposure.source_read_chars}; schema characters per binding summed: {exposure.tool_schema_chars}")
    print(f"  Memory preflight: {exposure.preflight_duration_ms:.2f} ms")
    print(f"  Retrieved paths later read: {len(exposure.retrieved_paths_read)} (overlap, not causal use)")
    print(f"  Read/search enrichments: {sum(e.status == 'used' for e in memory.enrichments)} used / {len(memory.enrichments)} attempted")
    print(f"  Fallback: {memory.fallback}")
    print(f"  Artifact: {result.run_dir / 'legion-memory.json'}")


def _render_solve_usage_summary(result: SolveResult) -> None:
    provenance = result.provenance
    print()
    print("Usage totals:")
    if provenance is None:
        print("  Model calls: unavailable")
        print("  Total tool calls: unavailable")
        print("  Commands: unavailable")
        print("  Total tokens: unavailable")
        return
    records = (*provenance.calls, *provenance.semantic_calls)
    input_tokens = sum(call.input_tokens or 0 for call in records)
    output_tokens = sum(call.output_tokens or 0 for call in records)
    cached_tokens = sum(call.cached_tokens or 0 for call in provenance.calls)
    tool_counts: dict[str, int] = {}
    for call in provenance.tool_calls:
        tool_counts[call.tool_name] = tool_counts.get(call.tool_name, 0) + 1
    print(f"  Model calls: {len(provenance.calls)}")
    print(f"  Jev calls: {len(provenance.semantic_calls)}")
    print(f"  Total tool calls: {len(provenance.tool_calls)}")
    print(
        "  Tools: "
        + (
            ", ".join(
                f"{name}={count}" for name, count in sorted(tool_counts.items())
            )
            or "none"
        )
    )
    print(f"  Commands: {json.dumps(list(provenance.commands), ensure_ascii=False)}")
    print(f"  Input tokens: {input_tokens}")
    print(f"  Output tokens: {output_tokens}")
    print(f"  Cached input tokens: {cached_tokens}")
    print(f"  Total tokens: {input_tokens + output_tokens}")
    missing = sum(call.input_tokens is None or call.output_tokens is None for call in records)
    if missing:
        print(f"  Token usage incomplete: {missing} call(s) have unreported usage; totals include known tokens only.")


def _render_memory_retrieval(
    result: MemoryRetrievalResult,
    *,
    context_file: Path | None = None,
) -> None:
    """Render stable retrieval logs without trusting database text as terminal data."""

    print(f"Legion Memory retrieval: {result.status.value}")
    print(f"  Memory used: {'yes' if result.status is MemoryRetrievalStatus.USED else 'no'}")
    print(f"  Outcome: {result.outcome.value}")
    print(f"  Summary: {_safe_log_value(result.summary, 500)}")
    print(f"  Memory file: {result.memory_file}")
    print(f"  Indexed SHA: {result.indexed_sha or 'unavailable'}")
    print(f"  Search modes: {', '.join(result.search_modes) or 'none'}")
    print(
        "  Query terms: "
        + (", ".join(_safe_log_value(term, 80) for term in result.query_terms) or "none")
    )
    print(f"  Lexical candidates: {result.lexical_candidates}")
    print(f"  Graph-expanded candidates: {result.expanded_candidates}")
    print(f"  Retrieved: {result.returned}/{result.total_candidates}")
    print(f"  Omitted: {result.omitted}")
    print(f"  Truncated: {'yes' if result.truncated else 'no'}")
    print(f"  Context characters: {result.context_chars}")
    print("  Usage meaning: retrieved context, not measured improvement")
    print(f"  Unresolved relationships skipped: {result.unresolved_edges}")
    print(f"  Ranking duration: {result.ranking_duration_ms:.2f} ms")
    if report := result.relevance_filter:
        print(f"  Jev relevance filter: {report.status} (mode={report.mode})")
        print(f"  Candidate files: {len(report.candidate_files)}")
        print(f"  Relevant files after Jev filter: {len(report.retained_files)}")
        for path in report.retained_files:
            print(f"    - {_safe_log_value(path, 500)}")
        print(f"  Discarded by Jev: {report.discarded_items} retrieval items / {len(report.rejected_files)} files")
        print(f"  Unjudged/withheld: {report.withheld_items} retrieval items / {len(report.withheld_files)} files")
        print(f"  Omitted by context budget: {report.context_omitted_items} retrieval items")
        if report.mode == "off":
            print("  Jev is off: retained files are lexical results, not Jev-approved.")
        if report.mode == "shadow":
            print(f"  Shadow would discard: {len(report.would_discard_files)} files (not applied)")
        if report.reason:
            print(f"  Filter reason: {_safe_log_value(report.reason, 100)}")
        print(f"  Jev time: {report.latency_ms:.2f} ms")
        print(f"  Jev input tokens: {report.input_tokens if report.input_tokens is not None else 'unknown'}")
        print(f"  Jev output tokens: {report.output_tokens if report.output_tokens is not None else 'unknown'}")
    if context_file is not None:
        print(f"  Context file: {context_file}")
    print(f"  Duration: {result.duration_ms:.2f} ms")
    if result.items:
        print("  Retrieved memories:")
        for item in result.items:
            location = (
                f"{_safe_log_value(item.file_path, 300)}:"
                f"{item.line_start}-{item.line_end}"
            )
            print(
                f"    {item.rank}. {_safe_log_value(item.kind, 40)} "
                f"{_safe_log_value(item.qualified_name, 500)}"
            )
            print(f"       Location: {location}")
            print(f"       Score: {item.score:.3f}")
            print(f"       Why: {', '.join(item.reasons)}")
    if result.warnings:
        print("  Warnings:")
        for warning in result.warnings:
            print(f"    - {_safe_log_value(warning, 500)}")


def _safe_log_value(value: object, limit: int) -> str:
    rendered = "".join(
        character if character.isprintable() else " " for character in str(value)
    )
    return rendered if len(rendered) <= limit else rendered[: limit - 1] + "…"
