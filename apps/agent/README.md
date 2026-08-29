# Sage backend

The Python package contains the trusted controller, isolated workspace
preparation, Docker sandbox, provider-neutral repository tools, artifact store,
and the sequential V2 Solver/Reviewer runtime. V2 is the only runtime. The
OpenAI-backed Solver works through a bounded tool loop, deterministic checks
verify its candidate, and the Gemini-backed Reviewer independently evaluates
the result. Repairable findings return to a fresh Solver session.

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
