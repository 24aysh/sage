# IssueAgent backend

The Python package contains the trusted V0 controller, isolated workspace
preparation, Docker sandbox, provider-neutral repository tools, artifact store,
and the temporary OpenAI Agents SDK adapter.

Run from the repository root:

```bash
uv sync --project apps/agent
uv run --project apps/agent issue-agent --help
uv run --project apps/agent pytest
```

See the root `README.md` for sandbox setup, configuration, and end-to-end usage.
