# Sage backend

The Python package contains the trusted controller, isolated workspace
preparation, Docker sandbox, provider-neutral repository tools, artifact store,
and the sequential V2 Admission/Solver/Reviewer runtime. V2 is the only runtime.
Admission is disabled by default; when enabled, it reuses the Solver model and
persists read-only evidence for the tool-driven Solver. Gemini remains the
independent Reviewer.

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
`specs/22_V2_DEFAULT_RUNTIME_TESTING.md` for current V2 configuration and
end-to-end testing.
