"""Issue, repair, and review message assembly; source remains evidence."""

from __future__ import annotations

from sage.errors import AgentRuntimeError
from sage.harness.context.run import SolverContext


def prepare_solver_message(message: str, *, stage: str, context: SolverContext) -> str:
    """Reset visibility for this history, add repair locators, and enforce its cap."""
    if context.memory is not None:
        packet = context.memory.begin_session(initial_visible=stage != "solver-repair")
        if stage == "solver-repair" and packet:
            message += "\n\n<legion-repair-context>\n" + packet + "\n</legion-repair-context>"
    input_cap = (
        context.settings.repair_input_chars
        if stage == "solver-repair"
        else context.settings.solver_input_chars
    )
    if len(message) > input_cap:
        raise AgentRuntimeError("Solver context exceeds the configured safe input cap.")
    return message


def build_solver_message(
    *,
    base_sha: str,
    issue_text: str,
    memory_context: str | None = None,
) -> str:
    """Build the initial untrusted Issue envelope for a Solver session."""

    issue = (
        f"Accepted base SHA: {base_sha}\n\n"
        "<untrusted-issue>\n"
        f"{issue_text}\n"
        "</untrusted-issue>"
    )
    if memory_context is None:
        return issue
    return (
        f"{issue}\n\n"
        "<untrusted-legion-memory>\n"
        f"{memory_context}\n"
        "</untrusted-legion-memory>"
    )


def build_repair_message(
    *,
    issue_text: str,
    plan_json: str,
    candidate_diff: str,
    findings_json: str,
) -> str:
    """Build bounded feedback for a fresh Solver repair tool loop."""

    return (
        "Repair the current workspace for the blocking Reviewer findings. "
        "Inspect the actual files and diff before editing. Revise the plan if "
        "the approach changes materially.\n\n"
        f"<untrusted-issue>\n{issue_text}\n</untrusted-issue>\n\n"
        f"<saved-plan>\n{plan_json}\n</saved-plan>\n\n"
        f"<current-diff>\n{candidate_diff}\n</current-diff>\n\n"
        f"<review-findings>\n{findings_json}\n</review-findings>"
    )


def build_review_message(
    *,
    issue_text: str,
    plan_json: str,
    changed_files_json: str,
    candidate_diff: str,
    verification_json: str,
    solver_summary: str,
    max_chars: int,
) -> str:
    """Build the Reviewer's bounded authoritative candidate packet."""

    packet = (
        f"<untrusted-issue>\n{issue_text}\n</untrusted-issue>\n\n"
        f"<saved-solver-plan>\n{plan_json}\n</saved-solver-plan>\n\n"
        f"<actual-changed-files>\n{changed_files_json}\n"
        "</actual-changed-files>\n\n"
        f"<actual-git-diff>\n{candidate_diff}\n</actual-git-diff>\n\n"
        f"<actual-verification>\n{verification_json}\n"
        "</actual-verification>\n\n"
        f"<solver-summary>\n{solver_summary}\n</solver-summary>"
    )
    if len(packet) > max_chars:
        raise AgentRuntimeError("Reviewer context exceeds the configured safe input cap.")
    return packet
