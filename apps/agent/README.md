# Sage backend

The Python package contains the trusted controller, isolated workspace
preparation, Docker sandbox, provider-neutral repository tools, artifact store,
and the sequential V2 Solver/Reviewer runtime. V2 is the only runtime. The
OpenAI-backed Solver works through a bounded tool loop, deterministic checks
verify its candidate, and the Gemini-backed Reviewer independently evaluates
the result. Repairable findings return to a fresh Solver session.

The optional SMRT memory engine is isolated under `sage.memory`. It stores a
sparse, content-addressed semantic overlay in PostgreSQL, builds a disposable
SQLite FTS5 index per solve, and gates repository exploration through a
run-scoped context forest. It is disabled by default with
`SAGE_MEMORY_ENABLED=false`; failures switch only that solve to ordinary
repository exploration.

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
end-to-end testing. See `specs/26_SAGE_SMRT_MEMORY_ENGINE_TESTING.md` for
memory migration, offline tests, canaries, and fallback checks.
