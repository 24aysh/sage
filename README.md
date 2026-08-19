# Sage

Sage is a GitHub-native issue-to-PR engineering agent in development. Its
core design keeps model judgment separate from deterministic repository work:
the agent decides what to inspect and change, while project-owned tools perform
every read, search, command, patch, and Git operation.

The currently supported end-to-end milestone is **V0.1: a local, single-agent
issue solver with a project-owned LangGraph runtime**. Given a committed local
Git repository and a Markdown or text issue, Sage creates an isolated clone,
runs one software-engineering agent against it through bounded tools, and
persists the candidate patch. The local command does not modify the source
checkout or interact with GitHub.

The V1.0 GitHub-native migration is now in progress. Its implemented foundation
can validate Issue-comment events, call GitHub through a bounded typed REST
client, authorize maintainers, reject existing Sage branches/PRs, and create or
reuse a gate status. It can also build a bounded current-Issue task file outside
the target checkout. The solver/publisher lifecycle, composite actions, and
installable workflow are not yet available, so `/sage solve` must not be
enabled in a production workflow. See
[`specs/10_V1.0_testing.md`](specs/10_V1.0_testing.md) for the exact status and
offline checks.

## Architecture

```text
local repository + issue.md
             │
             ▼
       sage CLI
             │
             ▼
     provider-neutral workflow
        ┌────┴───────────────┐
        ▼                    ▼
 AgentRuntime protocol   workspace manager
        │                isolated Git clone
 LangGraphRuntime            │
 custom StateGraph           │
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

The project-owned runtime under
`apps/agent/src/sage/runtimes/langgraph/` explicitly owns model calls,
tool routing, turn limits, validation, and termination. Domain models,
workflow, repository tools, sandboxing, and artifacts remain independent of
LangGraph and provider-specific response shapes.

## Repository layout

```text
apps/
  agent/   Python controller, repository tools, runtime adapter, and tests
  web/     Next.js product landing page
docker/
  sandbox/ Minimal repository execution image
examples/
  issue.md Issue input template
.sage/
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

## Manual testing

For a first-time, end-to-end walkthrough—including environment setup, sandbox
creation, a reproducible sample issue, artifact review, and troubleshooting—see
[`specs/03_V0_testing.md`](specs/03_V0_testing.md). The root `Makefile` keeps the
guide's common commands discoverable:

```bash
make help
```

For a complete first-time setup and live solve in one command:

```bash
make first-run \
  REPO=/absolute/path/to/committed/repository \
  ISSUE=/absolute/path/to/issue.md
```

The command loads `.env` when present or securely prompts for the API key, syncs
the Python environment, builds and smoke-tests Docker, runs deterministic
checks, and starts the solve. See
[`specs/06_V0.1_testing.md`](specs/06_V0.1_testing.md) for V0.1-specific graph
and migration checks.

Developers can verify the current V1.0 GitHub controller foundation without a
GitHub token, Docker, network call, or model call:

```bash
make github-test
```

## Backend setup

Install the locked Python environment from the repository root:

```bash
uv sync --project apps/agent
```

Build the default repository sandbox:

```bash
docker build \
  -t sage-sandbox:v0 \
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
uv run --project apps/agent sage solve \
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

Each invocation creates `.sage/runs/<run-id>/` containing:

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

Run only the current offline GitHub integration and CLI checks with:

```bash
make github-test
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
- **V0.1 — project-owned runtime:** replaces the bootstrap Agents SDK adapter
  with an explicit, tested LangGraph state machine while preserving V0 behavior.
- **V1 — GitHub Actions integration (in progress):** event validation, the
  model-free authorization/duplicate gate, REST boundary, status reuse, and
  safe Actions outputs are implemented. Bounded Issue-context assembly is also
  implemented. Solve orchestration, branch publication, draft PRs, composite
  actions, and the enabled workflow remain.
- **V2 — multi-agent workflow:** later work will extend project-owned
  orchestration with exploration, implementation, and review roles.

Sage still contains no GitHub App, enabled Actions workflow, database, queue,
checkpoint persistence, or multi-agent flow. V1.0's project-owned REST client
is used only by the trusted controller boundary.
