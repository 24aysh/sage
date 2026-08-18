# IssueAgent backend

The Python package contains the trusted V0 controller, isolated workspace
preparation, Docker sandbox, provider-neutral repository tools, artifact store,
and the V0.1 project-owned LangGraph runtime.

Run from the repository root:

```bash
uv sync --project apps/agent
uv run --project apps/agent issue-agent --help
uv run --project apps/agent pytest
```

Print the topology of the compiled runtime without an API call:

```bash
make graph
```

See the root `README.md` for sandbox setup, configuration, and end-to-end usage.
