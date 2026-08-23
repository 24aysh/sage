# Sage backend

The Python package contains the trusted controller, isolated workspace
preparation, Docker sandbox, provider-neutral repository tools, artifact store,
the V1 runtime, and the opt-in sequential V2 Planner/Solver/Reviewer runtime.

Run from the repository root:

```bash
uv sync --project apps/agent
uv run --project apps/agent sage --help
uv run --project apps/agent pytest
```

Print the topology of the compiled runtime without an API call:

```bash
make graph
make v2-graph
```

See the root `README.md` for setup and
`specs/15_SAGE_V2_PROTOTYPE_TESTING.md` for V2 configuration and end-to-end
testing.
