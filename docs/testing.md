# Testing Sage

This guide covers offline development checks, the Docker boundary, local live
solves, and the controlled GitHub rollout check. Run commands from the
repository root.

## Prerequisites

- Python 3.14;
- [`uv`](https://docs.astral.sh/uv/);
- Git;
- Docker only for sandbox and live-solve checks; and
- provider credentials only for a live solve.

Create local configuration without overwriting an existing file:

```bash
make env
```

Add `OPENAI_API_KEY` and `GEMINI_API_KEY` to `.env`. Keep
`SAGE_GOOGLE_MODEL_CONTEXT_APPROVED=true` only when sending the selected Issue
and repository context to the configured Google Reviewer is approved.

The canonical image tag changed to `sage-sandbox:v2`; rebuild it before a live
local run. If an existing private `.env` overrides `SAGE_SANDBOX_IMAGE`, update
that value as well. Use `make ENV_FILE=/dev/null sandbox-build` to verify the
tracked default independently of local overrides.

## Complete offline check

```bash
make setup
make check
```

`make check` runs the complete deterministic Python suite—including
architecture and Action/workflow policy tests—and compiles the package. It
does not need Docker, API keys, network access, or paid model calls.

For a direct equivalent:

```bash
LANGSMITH_TRACING=false uv run --project apps/agent \
  pytest -c apps/agent/pyproject.toml
uv run --project apps/agent python -m compileall -q apps/agent/src
```

## Focused checks

Use the narrowest relevant group while iterating:

```bash
uv run --project apps/agent pytest -c apps/agent/pyproject.toml \
  apps/agent/tests/agents apps/agent/tests/orchestration

uv run --project apps/agent pytest -c apps/agent/pyproject.toml \
  apps/agent/tests/repository apps/agent/tests/verification

make github-test
make actions-check
```

Print the generated Solver graph without a model call:

```bash
make graph
```

The graph should contain the bounded model/tool loop. It is not the outer
solve/verify/review workflow.

## Legion Memory Phase 1

Build or update a repository graph without provider credentials, Docker, MCP,
or network access:

```bash
make legion-memory REPO=/absolute/path/to/repository
```

To choose the SQLite file explicitly:

```bash
make legion-memory \
  REPO=/absolute/path/to/repository \
  MEMORY_FILE=/absolute/path/to/graph.sqlite3
```

The direct CLI equivalents are:

```bash
uv run --project apps/agent sage memory build \
  --repo /absolute/path/to/repository \
  --memory-file /absolute/path/to/graph.sqlite3

uv run --project apps/agent sage memory status \
  --repo /absolute/path/to/repository \
  --memory-file /absolute/path/to/graph.sqlite3
```

Without `MEMORY_FILE`, commands run from the Sage repository root use
`.sage/legion-memory/<repo>-<identity>/graph.sqlite3`. The build output reports
the resolved file, full/incremental/no-change decision, exact indexed SHA,
counts, languages, warnings, and duration. Legion Memory reads committed
`HEAD` blobs, so a build does not mutate the target repository and does not
accidentally label dirty worktree content with the accepted SHA.

Run only the graph suite while iterating:

```bash
LANGSMITH_TRACING=false uv run --project apps/agent \
  pytest -c apps/agent/pyproject.toml apps/agent/tests/legion_memory
```

For a manual smoke check, build once and expect `full`, rerun unchanged and
expect `no_change`, commit a source change and expect `incremental`, then run
`memory status`. Confirm node and edge counts are non-zero and `git status` in
the target repository is unchanged. The deterministic tests additionally
cover add/change/delete/rename reconciliation, stale and foreign databases,
corrupt schemas, SQL-shaped search input, every supported grammar, and every
native read-only tool adapter.

## Architecture checks

The AST guard verifies package ownership, dependency direction, empty package
initializers, removal of old implementation paths, file count, source size,
orchestrator size, and internal fan-out:

```bash
uv run --project apps/agent pytest -c apps/agent/pyproject.toml \
  apps/agent/tests/test_architecture.py
```

When this fails, fix the ownership violation. Do not weaken the allowlist to
hide a new reverse dependency.

## Docker sandbox

Build and smoke-test the same image used by local and hosted solves:

```bash
make sandbox-build
make sandbox-smoke
```

The smoke check starts a disposable container with networking disabled and
confirms the required Git, Python, and ripgrep tools.

Run configuration diagnostics without printing secrets:

```bash
make doctor
```

## Offline GitHub publication

Exercise the production branch/commit/draft-PR transaction against local Git
substitutes:

```bash
make github-smoke
```

The result must report `Model calls: 0` and `Network calls: 0`. The default
branch must remain unchanged, the Sage branch must be creation-only, and the
recorded pull request request must be a draft.

To use a saved patch and an existing local clone:

```bash
make github-smoke \
  REPO=/absolute/path/to/repository \
  PATCH=/absolute/path/to/diff.patch \
  BASE_REF=<exact-sha>
```

## Live local solve

The guided path validates inputs, credentials, sandbox, offline checks, solve
completion, artifacts, and candidate diff:

```bash
make first-run \
  REPO=/absolute/path/to/repository \
  ISSUE=/absolute/path/to/issue.md \
  BASE_REF=HEAD
```

For subsequent runs:

```bash
make solve REPO=/absolute/path/to/repository ISSUE=/absolute/path/to/issue.md
```

The source repository is not mutated. Sage prints the isolated run directory.
Inspect it with:

```bash
make run-status RUN_DIR=/absolute/path/to/.sage/runs/<run-id>
make run-test RUN_DIR=/absolute/path/to/.sage/runs/<run-id> \
  TEST_COMMAND="python3 -m unittest discover -v"
```

A successful change exits zero and has outcome `completed`. A valid run that
produces no repository change exits two from the CLI; the Make wrapper reports
that as a warning unless `REQUIRE_COMPLETED=true`.

Do not use live paid calls in the normal unit suite. If credentials, Docker, or
explicit Google context approval are unavailable, record the live check as not
run rather than claiming it passed.

## GitHub controller checks

Classify a fixture with no API or model call:

```bash
make github-event-check \
  EVENT=apps/agent/tests/fixtures/github/issue_solve.json
```

The installed workflow accepts exact `/sage solve` and `/sage fix` Issue
comments, rejects pull-request comments, rechecks authorization and duplicate
state before constructing model dependencies, and runs at the gate's exact
base SHA.

After the implementation is committed and the workflow's two local Sage Action
references are pinned to that immutable commit, use a disposable repository for
one controlled canary:

1. invoke one bounded Issue with `/sage solve`;
2. confirm authorization, exact base SHA, and one status-comment lifecycle;
3. confirm a creation-only `sage/issue-<number>` branch and draft PR;
4. confirm the uploaded diagnostic allowlist contains no checkout, Issue body,
   or credentials;
5. rerun finalization and confirm it is idempotent; and
6. clean up through normal repository maintenance, not through Sage.

The canary cannot be run until an immutable implementation commit exists.

## Expected run evidence

For a completed candidate, check at least:

- `metadata.json` binds the run to its base SHA and model;
- `solver-plan.json` points at the latest immutable plan revision;
- `candidate-snapshot.json` contains the Git-derived diff digest;
- `verification-summary.json` records required checks;
- `review.json` contains complete criterion coverage;
- `usage.json` records bounded model-call provenance;
- `terminal.json` records the terminal solve outcome; and
- `changed-files.json` and `diff.patch` match the candidate workspace.

GitHub diagnostic uploads are intentionally smaller than the local run
directory and remain a fixed allowlist in the workflow.

## Troubleshooting

`uv` cannot install dependencies:

- confirm network access for the initial `make setup`;
- use the checked-in lock file; and
- do not regenerate dependency versions for an architecture-only change.

Docker image missing:

```bash
make sandbox-build
```

Docker daemon unreachable: start Docker and rerun `docker info`.

Reviewer configuration rejected: configure `GEMINI_API_KEY` and explicitly
approve Google context use. Solver authentication or quota failures are
terminal provider outcomes; fix the provider project rather than adding an
unbounded retry.

Candidate rejected after review: compare `candidate-snapshot.json`,
`verification-summary.json`, `review.json`, and the final `diff.patch`. A base
SHA or diff-digest change is a safety failure, not a retry signal.

GitHub workflow failure: run `make actions-check`, then inspect only the
allowlisted diagnostic artifact and the bot-owned status comment. Provider
details and credentials are deliberately excluded.

Legion Memory status is `missing`: run `make legion-memory REPO=...` or pass
the same explicit `MEMORY_FILE` to both build and status. A stale-SHA or
foreign-repository error is intentional; rebuild for the selected repository
instead of reusing the database. For corruption or an unsupported schema,
move the bad local database aside and run the build command to create a fresh
one. A standalone build fails non-zero rather than silently claiming memory is
ready.

Check the installed workflow files, immutable Action pins, documentation, and
Docker availability with:

```bash
make github-doctor
```
