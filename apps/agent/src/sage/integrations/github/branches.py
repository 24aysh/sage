"""Deterministic GitHub branch naming."""


def issue_branch_name(issue_number: int) -> str:
    """Return the single Sage branch allocated to an Issue."""

    if issue_number < 1:
        raise ValueError("Issue number must be positive.")
    return f"sage/issue-{issue_number}"
