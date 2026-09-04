"""LangSmith trace metadata and human-readable agent activity logs."""

from __future__ import annotations

import logging
from collections.abc import Sequence

from langchain_core.runnables import RunnableConfig

from sage.domain.memory import LegionMemoryRunArtifact
from sage.domain.usage import AttemptKind, ModelCallRecord, ModelRole

_STAGE_ACTIVITIES = {
    "solver": "Plan, implement, and verify the Issue through repository tools",
    "solver-repair": "Repair the actual candidate from Reviewer findings",
    "review": "Review the actual candidate against the Issue and saved plan",
    "rereview": "Review the repaired candidate against the Issue and saved plan",
}


def workflow_trace_config(
    *,
    run_id: str,
    graph_name: str,
    model_profile: str,
) -> RunnableConfig:
    """Build safe root trace attributes while retaining trace schema labels."""

    return {
        "run_name": "Sage V2 Workflow",
        "tags": ["sage-v2", f"profile:{model_profile}"],
        "metadata": {
            "sage_run_id": run_id,
            "sage_graph": graph_name,
            "sage_runtime": "v2",
            "sage_model_profile": model_profile,
        },
        "recursion_limit": 80,
    }


def agent_trace_config(
    *,
    run_id: str | None,
    role: ModelRole,
    stage: str,
    attempt: AttemptKind,
    provider: str,
    model: str,
    call_number: int,
) -> RunnableConfig:
    """Build a named LangSmith span configuration for one model attempt."""

    metadata: dict[str, str | int] = {
        "sage_role": role.value,
        "sage_stage": stage,
        "sage_attempt": attempt.value,
        "sage_provider": provider,
        "sage_model": model,
        "sage_call_number": call_number,
    }
    if run_id is not None:
        metadata["sage_run_id"] = run_id
    return {
        "run_name": role.value.capitalize(),
        "tags": [
            "sage-agent",
            f"role:{role.value}",
            f"stage:{stage}",
            f"provider:{provider}",
        ],
        "metadata": metadata,
    }


def log_agent_activity(
    logger: logging.Logger,
    *,
    role: ModelRole,
    stage: str,
    attempt: AttemptKind,
    provider: str,
    model: str,
    call_number: int,
    max_calls: int | None = None,
) -> None:
    """Log what an agent is doing without exposing its prompt or context."""

    activity = _STAGE_ACTIVITIES.get(stage, f"Work on workflow stage {stage}")
    logger.info(
        _panel(
            f"{role.value.capitalize()}: activity",
            (
                ("Task", activity),
                ("Stage", stage),
                ("Attempt", attempt.value),
                ("Model", f"{provider}/{model}"),
                ("Call", call_number if max_calls is None else f"{call_number}/{max_calls}"),
            ),
        )
    )


def log_agent_finished(logger: logging.Logger, record: ModelCallRecord) -> None:
    """Log the bounded outcome of one model attempt without response content."""

    details: list[tuple[str, object]] = [
        ("Status", "completed" if record.outcome == "success" else "failed"),
        ("Stage", record.stage),
        ("Duration", f"{record.latency_ms / 1_000:.2f}s"),
    ]
    if record.input_tokens is not None or record.output_tokens is not None:
        details.append(
            (
                "Tokens",
                f"{record.input_tokens or 0} input / {record.output_tokens or 0} output",
            )
        )
    if record.error_category is not None:
        details.append(("Error", record.error_category))
    if record.status_code is not None:
        details.append(("HTTP", record.status_code))
    if record.request_id is not None:
        details.append(("Request", record.request_id))
    logger.info(
        _panel(
            f"{record.role.value.capitalize()}: finished",
            details,
        )
    )


def log_agent_result(
    logger: logging.Logger,
    *,
    role: ModelRole,
    details: Sequence[tuple[str, object]],
) -> None:
    """Log a safe, domain-level summary of an agent's structured decision."""

    logger.info(_panel(f"{role.value.capitalize()}: result", details))


def log_legion_memory(
    logger: logging.Logger,
    artifact: LegionMemoryRunArtifact,
) -> None:
    """Log stable graph and retrieval summaries without graph payload content."""

    build = artifact.build
    graph_details: list[tuple[str, object]] = []
    if build is None:
        graph_details.extend(
            (
                ("Status", "unavailable"),
                ("Failure", artifact.failure_category or "unavailable"),
                ("Memory file", artifact.resolved_memory_file),
            )
        )
    else:
        graph_details.extend(
            (
                ("Build", build.build_type.value),
                ("Base", build.indexed_sha[:12]),
                (
                    "Files",
                    f"{build.files_parsed} updated / {build.files_indexed} indexed",
                ),
                ("Graph", f"{build.total_nodes} nodes / {build.total_edges} edges"),
                ("Memory file", artifact.resolved_memory_file),
            )
        )
    title = "Legion Memory: graph ready" if build else "Legion Memory: graph unavailable"
    logger.info(_panel(title, graph_details))

    retrieval = artifact.retrieval
    paths = (
        tuple(dict.fromkeys(item.file_path for item in retrieval.items))[:5]
        if retrieval is not None
        else ()
    )
    logger.info(
        _panel(
            "Legion Memory: retrieval",
            (
                ("Status", artifact.status.value),
                (
                    "Search",
                    ", ".join(retrieval.search_modes) if retrieval else "not run",
                ),
                (
                    "Matches",
                    (
                        f"{retrieval.returned} returned / "
                        f"{retrieval.total_candidates} considered"
                        if retrieval
                        else "0 returned / 0 considered"
                    ),
                ),
                ("Relevant paths", ", ".join(paths) or "none"),
                ("Fallback", artifact.fallback),
            ),
        )
    )


def _panel(title: str, details: Sequence[tuple[str, object]]) -> str:
    lines = [title]
    for index, (label, value) in enumerate(details):
        branch = "└─" if index == len(details) - 1 else "├─"
        rendered = (
            str(value).replace("\x00", "").replace("\r", " ").replace("\n", " ")
        )
        lines.append(f"  {branch} {label}: {rendered[:500]}")
    return "\n".join(lines)
