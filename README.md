# IssueAgent

IssueAgent is a GitHub-native issue-to-PR engineering agent in development. Its
core design keeps model judgment separate from deterministic repository work:
the agent decides what to inspect and change, while project-owned tools perform
every read, search, command, patch, and Git operation.

The current milestone is **V0: a local, single-agent issue solver**. Given a
committed local Git repository and a Markdown or text issue, V0 creates an
isolated clone, runs one software-engineering agent against it through bounded
tools, and persists the candidate patch. It does not modify the source checkout
or interact with GitHub.

## Architecture

```text
local repository + issue.md
             │
             ▼
       issue-agent CLI
             │
             ▼
     provider-neutral workflow
        ┌────┴───────────────┐
        ▼                    ▼
 AgentRuntime protocol   workspace manager
        │                isolated Git clone
 OpenAI Agents SDK           │
  adapter (V0 only)          │
        └────────┬───────────┘
                 ▼
       repository tool layer
 tree · search · read · patch · command · diff
                 │
                 ▼
       disposable Docker sandbox
       /workspace · no network · no provider secret
                 │
                 ▼
       candidate clone + diff.patch
```

The OpenAI Agents SDK is isolated under
`apps/agent/src/issue_agent/runtimes/openai_agents/`. Domain models, workflow,
repository tools, sandboxing, and artifacts do not depend on the SDK. This is a
deliberate V0 bootstrap boundary, not the long-term orchestration architecture.

## Repository layout

```text
apps/
  agent/   Python controller, repository tools, runtime adapter, and tests
  web/     Next.js product landing page
docker/
  sandbox/ Minimal repository execution image
examples/
  issue.md Issue input template
.issue-agent/
  runs/    Local run artifacts (created at runtime and ignored by Git)
```

## Prerequisites

- Python 3.14 (the version available in the current development environment)
- [uv](https://docs.astral.sh/uv/)
- Docker with a reachable daemon
- Git
- Node.js and npm
- an OpenAI API key

The original design targeted Python 3.13; this bootstrap uses Python 3.14 at the
repository owner's request.

## Backend setup

Install the locked Python environment from the repository root:

```bash
uv sync --project apps/agent
```

Build the default repository sandbox:

```bash
docker build \
  -t issue-agent-sandbox:v0 \
  -f docker/sandbox/Dockerfile \
  .
```

Export controller configuration. V0 intentionally does not use a dotenv
dependency:

```bash
export OPENAI_API_KEY="your-key"
export OPENAI_MODEL="gpt-5.3-codex"
```

All supported values are documented in [.env.example](.env.example). The model
can be changed with `OPENAI_MODEL` without changing application code.

## Solve an issue

```bash
uv run --project apps/agent issue-agent solve \
  --repo /absolute/path/to/repository \
  --issue-file /absolute/path/to/issue.md
```

Optional flags:

- `--base-ref <ref>` selects a committed revision; the default is `HEAD`.
- `--sandbox-image <image>` selects a repository-specific Docker image.
- `--debug` enables detailed controller logging and tracebacks.

The input repository may contain uncommitted work, but V0 intentionally clones
only the selected committed revision. The original checkout is never the agent's
writable workspace.

## Run artifacts

Each invocation creates `.issue-agent/runs/<run-id>/` containing:

```text
request.json
metadata.json
issue.md
agent-final.json
changed-files.json
diff.patch
repo/
```

`repo/` is the isolated candidate checkout. `changed-files.json` and
`diff.patch` are derived from Git rather than trusted from the model's claims.
No provider key or complete host environment is persisted.

CLI exit codes are:

- `0` — the agent completed with a non-empty diff;
- `1` — configuration, infrastructure, sandbox, or runtime failure;
- `2` — the agent completed without a repository change.

## Backend verification

```bash
uv run --project apps/agent pytest
uv run --project apps/agent python -m compileall -q apps/agent/src
```

The unit suite uses temporary repositories and fakes at provider and Docker
boundaries; it does not make paid API calls.

## Landing page

The web app explains the product and roadmap; it does not execute the agent.

```bash
cd apps/web
npm install
npm run dev
```

Useful checks:

```bash
npm run lint
npx tsc --noEmit
npm run build
```

## Roadmap boundaries

- **V0 — local issue solver:** the implementation in this repository. One agent,
  a local controller, an isolated Docker workspace, and persistent patch
  artifacts.
- **V1 — GitHub Actions integration:** later work will add authorized issue
  triggers, pinned GitHub checkout, branch publishing, and draft pull requests
  around the existing controller and sandbox.
- **V2 — multi-agent workflow:** later work will replace the temporary runtime
  with project-owned orchestration for exploration, implementation, and review.

V0 deliberately contains no GitHub App, Actions workflow, HTTP API, database,
queue, LangGraph runtime, or multi-agent flow.
