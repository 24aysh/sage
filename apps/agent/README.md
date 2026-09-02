# Sage backend

This package is the trusted Python controller for Sage. It owns typed domain
contracts, explicit Solver and Reviewer roles, deterministic orchestration,
isolated repository capabilities, verification, providers, run evidence, and
the GitHub lifecycle.

From the repository root:

```bash
make setup
uv run --project apps/agent sage --help
make check
make graph
```

Production construction starts in `sage/composition.py`; local and GitHub
resource lifecycles are in `sage/workflows/`; agent behavior is in
`sage/agents/`; and the trusted outer control loop is
`sage/orchestration/solve.py`.

See the root [README](../../README.md),
[architecture guide](../../docs/architecture.md), and
[testing guide](../../docs/testing.md).
