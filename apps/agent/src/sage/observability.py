"""LangSmith trace metadata and human-readable V2 agent activity logs."""

from __future__ import annotations

import logging
from collections.abc import Sequence

from langchain_core.runnables import RunnableConfig

from sage.domain.usage import AttemptKind, ModelCallRecord, ModelRole

_STAGE_ACTIVITIES = {
    "admission": "Assess autonomous readiness and collect reusable context",
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
    """Build safe root trace attributes for one V2 workflow."""

    return {
        "run_name": "Sage V2 Workflow",
        "tags": ["sage-v2", f"profile:{model_profile}"],
        "metadata": {
            "sage_run_id": run_id,
            "sage_graph": graph_name,
            "sage_runtime": "v2-prototype",
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


def _panel(title: str, details: Sequence[tuple[str, object]]) -> str:
    lines = [title]
    for index, (label, value) in enumerate(details):
        branch = "└─" if index == len(details) - 1 else "├─"
        rendered = (
            str(value).replace("\x00", "").replace("\r", " ").replace("\n", " ")
        )
        lines.append(f"  {branch} {label}: {rendered[:500]}")
    return "\n".join(lines)
