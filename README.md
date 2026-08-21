# Sage

Sage is a GitHub-native issue-to-draft-PR engineering agent. Its
core design keeps model judgment separate from deterministic repository work:
the agent decides what to inspect and change, while project-owned tools perform
every read, search, command, patch, and Git operation.

V1.0 adds the complete GitHub Actions controller: an authorized maintainer can
comment exactly `/sage solve` or `/sage fix` on an Issue, Sage solves against a
recorded default-branch commit in its isolated Docker workspace, then publishes
a creation-only `sage/issue-<number>` branch and draft Pull Request. Gate,
solve, and finalizer jobs have separate permissions; action dependencies are
pinned to immutable commits; the model secret is available only to the solve
job; and the sandbox receives neither GitHub nor model credentials.

The local V0.1 command remains supported and does not modify the source checkout
or interact with GitHub. The V1.0 implementation passes deterministic local
verification. The first controlled live canary reached OpenAI but was rejected
with HTTP 429 before a model response; Sage now distinguishes exhausted
credits/account limits from temporary request/token throttling and reports the
matching safe recovery action. The remaining canary is still required before
production rollout. See
[`specs/10_V1.0_testing.md`](specs/10_V1.0_testing.md) for the exact installation,
user-side setup, provider recovery, and canary procedure.

## Architecture

```text
GitHub Issue comment                   local repository + issue.md
        │                                          │
        ▼                                          ▼
authorize · deduplicate · exact base            sage CLI
        │                                          │
        └──────────────────┬───────────────────────┘
                           ▼
                 provider-neutral workflow
                    ┌──────┴────────────┐
                    ▼                   ▼
             AgentRuntime          workspace manager
             LangGraphRuntime      isolated Git clone
                    └──────┬────────────┘
                           ▼
                 repository tool layer
          tree · search · read · patch · command · diff
                           │
                           ▼
                 disposable Docker sandbox
             /workspace · no network · no credentials
                           │
               ┌───────────┴────────────┐
               ▼                        ▼
     local candidate artifacts   validated creation-only branch
                                    + draft Pull Request
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

Developers can verify the V1.0 GitHub controller, publisher, composite actions,
and workflow policies without a GitHub token, network call, or model call:

```bash
make v1-check
```

Use `make github-doctor` to check the local workflow installation and Docker
availability before a live canary.

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
can be changed with `OPENAI_MODEL` without changing application code. Temporary
OpenAI failures use bounded SDK backoff; `OPENAI_MAX_RETRIES` defaults to `2`
and accepts values from `0` through `10`. Increasing retries does not repair
exhausted credits or organization/project limits.

The GitHub workflow reads the optional non-secret `OPENAI_MODEL` repository
variable, defaulting to `gpt-5.3-codex`. Each solve logs the selected model and
a safe API-key state (`configured`, `accepted_by_api`, or
`invalid_or_unauthorized`) without logging the key or a key fingerprint. A 429
log also includes only OpenAI's available retry/reset headers.

Before publication, Sage keeps Git's whitespace gate enabled. If it rejects a
candidate, the Actions error includes bounded, control-safe Git stderr/stdout so
the offending filename and line remain visible. The coding agent is also told
to run `git diff --check HEAD --` and remove transient caches before finishing.

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
- **V1 — GitHub Actions integration:** event validation, model-free
  authorization/deduplication, bounded Issue context, solve orchestration,
  creation-only branch publication, draft PR reconciliation, terminal status
  repair, pinned composite actions, and the installable workflow are
  implemented. The first live run reached the provider boundary; production
  enablement remains gated on resolving the provider's HTTP 429 rejection and
  completing the documented canary.
- **V2 — multi-agent workflow:** later work will extend project-owned
  orchestration with exploration, implementation, and review roles.

Sage still contains no long-running GitHub App service, database, queue,
checkpoint persistence, auto-merge, or multi-agent flow. V1.0 uses the
job-scoped GitHub Actions token through its project-owned REST client at the
trusted controller boundary.
