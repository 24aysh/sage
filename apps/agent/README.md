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

`sage/harness/` owns the support an agent receives throughout an Issue:
`context/` assembles bounded packets and persistent role guidance; `memory/`
indexes committed source and supplies lexical graph navigation; `jev/` optionally
selects bounded read-only observations. Memory requires no credentials or
external store. Orchestration retains all plan, verification, and review gates.

`sage/cli/` mirrors the solve, memory, and GitHub commands. Within
`sage/harness/memory/`, `indexing.py` owns committed-source provenance and graph
builds, `service.py` owns validated operations, `retrieval.py` owns Issue ranking,
and `session.py` owns run visibility. `bindings.py` analyzes source dependency
bindings; it does not construct Sage services.

See the root [README](../../README.md),
[architecture guide](../../docs/architecture.md), and
[testing guide](../../docs/testing.md).
