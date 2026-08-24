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
verification and a controlled GitHub canary has produced a correct draft Pull
Request from an Issue command. Earlier canaries also verified safe recovery for
provider HTTP 429 responses and actionable Git whitespace diagnostics. See
[`specs/10_V1.0_testing.md`](specs/10_V1.0_testing.md) for the exact installation,
user-side setup, provider recovery, and canary procedure. The completed goals,
as-built contracts, release audit, and merge handoff are recorded in
[`specs/12_V1.0_as_built_and_release.md`](specs/12_V1.0_as_built_and_release.md).

An opt-in V2 sequential prototype now reuses the V1 repository-tool loop for
an OpenAI Solver, persists the Solver's plan before mutation, and requires an
independent Gemini Reviewer pass over the actual Git candidate. V1 remains the
default. See
[`specs/17_SAGE_V2_TOOL_DRIVEN_TESTING.md`](specs/17_SAGE_V2_TOOL_DRIVEN_TESTING.md)
before enabling V2 or making a paid canary call.

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
- a Gemini API key when using V2
- optionally, a LangSmith API key for hosted V2 traces

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

Run the complete offline V2 prototype checks and print its sequential graph:

```bash
make v2-check
make v2-graph
```

After configuring the OpenAI and Gemini provider keys and approving Google
model context use, run the complete live V2 workflow against the checked-in
disposable fixture:

```bash
make v2-first-run
```

The target creates a temporary Git repository from `v2-manual-test/project`,
uses `v2-manual-test/issue.md`, requires a completed non-empty candidate, and
validates the retained artifacts and diff under `.sage/runs/`.

To run the same strict workflow against another committed repository and Issue:

```bash
make v2-first-run \
  REPO=/absolute/path/to/repository \
  ISSUE=/absolute/path/to/issue.md
```

During a run, INFO logs render readable `Admission: activity`, research-tool,
`Solver: activity`, verification, and `Reviewer: finished` events. Admission
reuses the Solver model, inspects only through read-only tools, and persists a
bounded evidence snapshot for Solver instead of making Solver repeat the
initial repository scan. Optional LangSmith tracing records Admission, Solver,
Reviewer, and tool spans; configuration and data-boundary guidance is in the
V2 testing guide.

External coding research is optional. To enable the current Tavily adapter,
keep the target Docker sandbox offline and configure only the trusted
controller:

```bash
export SAGE_WEB_SEARCH_PROVIDER=tavily
export SAGE_WEB_SEARCH_API_KEY="your-tavily-key"
```

Without those values, Admission and Solver continue with repository-local
evidence. See
[`specs/19_SAGE_V2_ADMISSION_AND_RESEARCH_TESTING.md`](specs/19_SAGE_V2_ADMISSION_AND_RESEARCH_TESTING.md)
for clarification, context-artifact, research, and GitHub testing.

Use `make github-doctor` to check the local workflow installation and Docker
availability before a live canary.

When a saved candidate already exists, test the complete Git branch, commit,
creation-only push, and draft-PR request without spending model calls or
contacting GitHub:

```bash
make v2-github-smoke

make v2-github-smoke \
  REPO=/absolute/path/to/repository \
  PATCH=/absolute/path/to/diff.patch \
  BASE_REF=<artifact-base-sha> \
  ISSUE_NUMBER=5
```

The second form replays a downloaded run patch against an isolated local clone.
See the offline publication section in the V2 testing guide for inspection and
limitations.

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
export OPENAI_MODEL="gpt-5.4-mini"
```

All supported values are documented in [.env.example](.env.example). The V1
model can be changed with `OPENAI_MODEL`. V2 has exactly two configured model
roles: Admission reuses the Solver model, while Solver and Reviewer can be
changed with `SAGE_V2_SOLVER_MODEL` and `SAGE_V2_REVIEWER_MODEL` without
editing application code. Temporary
OpenAI failures use bounded SDK backoff; `OPENAI_MAX_RETRIES` defaults to `2`
and accepts values from `0` through `10`. Increasing retries does not repair
exhausted credits or organization/project limits.

The GitHub workflow reads the optional non-secret `OPENAI_MODEL` repository
variable, defaulting to `gpt-5.4-mini`. Each solve logs the selected model and
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
boundaries; it does not make live external API calls.

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
  implemented. A controlled live run completed the provider, sandbox,
  publication, and draft Pull Request path successfully.
- **V2 — multi-agent workflow:** the opt-in sequential
  Admission/Solver/Reviewer graph uses two configured model roles, persisted
  read-only Admission context, structured repository edits, optional safe
  controller-side research, Git-derived candidates, independent review, and
  progress-based repairs. Parallel workers, merge agents, and automatic merge
  remain deferred.

Sage still contains no long-running GitHub App service, database, queue,
checkpoint persistence, auto-merge, or parallel worker flow. V1.0 uses the
job-scoped GitHub Actions token through its project-owned REST client at the
trusted controller boundary.

Sage uses standard GitHub Actions and the job-scoped `GITHUB_TOKEN`; it does
not require a paid GitHub API, personal access token, or separately hosted
service. Model access comes from the operator-provided `OPENAI_API_KEY`. Sage
does not enable billing or purchase capacity; whether that account's model
allocation is free, credit-backed, or billable is controlled by the account
owner and OpenAI.
