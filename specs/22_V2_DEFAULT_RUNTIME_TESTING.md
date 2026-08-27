# V2 Default Runtime Testing Guide

## Purpose

This guide verifies the implemented V2 runtime: the optional Admission role,
the tool-driven Solver, deterministic verification, and the independent
Reviewer. The checks are ordered from fast offline validation to a live local
solve and GitHub publication smoke test.

V2 is the only supported runtime. `SAGE_RUNTIME` can be omitted or set to
`v2`. Admission is disabled by default and is enabled only with
`SAGE_V2_ADMISSION_ENABLED=true`.

## Prerequisites

Run commands from the repository root. Install:

- Git;
- `uv`;
- Docker with a reachable daemon; and
- Python dependencies from the checked-in lock file.

Set up the project and sandbox:

```bash
make env
make setup
make sandbox-build
make sandbox-smoke
```

For live model-backed tests, configure these values in `.env` or export them
in the shell:

```dotenv
OPENAI_API_KEY=...
GEMINI_API_KEY=...
SAGE_GOOGLE_MODEL_CONTEXT_APPROVED=true
```

The OpenAI credential is used by the Solver and by Admission when Admission is
enabled. The Gemini credential is used by the independent Reviewer. Do not
commit `.env`.

## 1. Verify configuration defaults

Run the focused configuration tests:

```bash
uv run --project apps/agent pytest \
  -c apps/agent/pyproject.toml \
  apps/agent/tests/test_config.py
```

The test must pass and confirms:

- omitted or blank `SAGE_RUNTIME` resolves to `v2`;
- `v1`, `v2-prototype`, and unknown runtime names are rejected;
- Admission defaults to disabled;
- `SAGE_V2_ADMISSION_ENABLED=true` enables Admission;
- both model-provider credentials are required for a live V2 configuration;
  and
- the removed `OPENAI_MODEL` value does not select the Solver model.

The active Solver and Reviewer model settings are
`SAGE_V2_SOLVER_MODEL` and `SAGE_V2_REVIEWER_MODEL`.

## 2. Run the complete offline check

```bash
make v2-check
```

This command runs the V2 unit and integration tests, GitHub Action policy
tests, and Python bytecode compilation. It does not require paid model calls.
A successful run exits with status 0.

For the broadest deterministic repository check, also run:

```bash
make check
```

## 3. Inspect the agent graphs

The top-level workflow is deterministic Python orchestration. LangGraph is
used inside each Admission or Solver tool session.

Print the shared per-session tool-loop Mermaid graph:

```bash
make graph
```

Validate the V2 routing and candidate helpers:

```bash
make v2-graph
```

Both commands are offline. The shared graph should contain an agent decision
node, a tool node, a finalization node, and conditional routing between them.

## 4. Test the default path without Admission

Create or select a small local Git repository and write an Issue file that
contains an objective and acceptance criteria. Confirm the target repository
is clean before the run.

Ensure Admission is unset or explicitly false:

```bash
unset SAGE_V2_ADMISSION_ENABLED
make first-run REPO=/absolute/path/to/repository \
  ISSUE=/absolute/path/to/issue.md
```

Equivalent explicit configuration is:

```bash
SAGE_V2_ADMISSION_ENABLED=false \
make first-run REPO=/absolute/path/to/repository \
  ISSUE=/absolute/path/to/issue.md
```

Expected behavior:

1. configuration resolves to V2;
2. no Admission model session runs;
3. the Solver saves a typed plan before changing files;
4. the controller derives the candidate from Git;
5. configured verification runs in the network-disabled sandbox;
6. Gemini independently reviews the bounded candidate packet; and
7. a successful result remains an uncommitted diff in the isolated run
   workspace.

The command prints the run directory. Inspect it with:

```bash
make run-status RUN_DIR=/absolute/path/to/run
```

In `usage.json`, `admission_sessions` must be `0`. No Admission context
artifact should exist.

## 5. Test the Admission opt-in path

Run the same type of bounded Issue with Admission enabled:

```bash
SAGE_V2_ADMISSION_ENABLED=true \
make first-run REPO=/absolute/path/to/repository \
  ISSUE=/absolute/path/to/issue.md
```

Expected behavior when Admission returns `READY`:

1. Admission receives read-only repository and configured research tools;
2. Admission saves a validated context snapshot before returning;
3. the controller validates its evidence and digest;
4. the Solver receives the bounded Admission context;
5. the Reviewer receives the controller-built review packet containing the
   relevant Admission evidence; and
6. `usage.json` records at least one Admission session.

Admission and Solver do not have a direct conversation. The controller passes
validated context artifacts from Admission to Solver. Reviewer findings reach
a fresh Solver repair session only after controller validation.

To test an Admission stop, use an Issue that intentionally omits a required
product decision. No repository mutation should occur, and the terminal
artifacts should contain focused clarification questions or the relevant
blocked disposition.

## 6. Verify repair behavior

Use a bounded Issue with deterministic checks capable of detecting an
incorrect implementation. During a repair cycle, verify:

- required verification failures are converted into a controller-built repair
  packet;
- a repair starts a fresh Solver tool loop;
- Reviewer blocking findings are also validated and passed through a fresh
  repair packet;
- the candidate is re-derived from Git after repair; and
- an unchanged candidate with the same failure stops through no-progress
  detection instead of looping indefinitely.

Offline coverage for these cases is included in:

```bash
uv run --project apps/agent pytest \
  -c apps/agent/pyproject.toml \
  apps/agent/tests/runtimes/v2/test_runtime.py \
  apps/agent/tests/workflow/test_solve.py
```

## 7. Verify runtime-name rejection

These commands must fail configuration before a solve begins:

```bash
SAGE_RUNTIME=v1 make solve \
  REPO=/absolute/path/to/repository ISSUE=/absolute/path/to/issue.md

SAGE_RUNTIME=v2-prototype make solve \
  REPO=/absolute/path/to/repository ISSUE=/absolute/path/to/issue.md
```

The error should direct the operator to `SAGE_RUNTIME=v2`. Omitting the
variable must select V2.

## 8. Test GitHub publication without model calls

Exercise branch creation, commit creation, and draft pull-request intent using
the offline publication smoke test:

```bash
make v2-github-smoke
```

To test a saved patch against a local clone and Git remote:

```bash
make v2-github-smoke \
  REPO=/absolute/path/to/repository \
  PATCH=/absolute/path/to/candidate.diff \
  BASE_REF=<commit-or-branch>
```

The smoke test must report zero model calls and zero network calls. It also
verifies `/dev/null` patch headers used for created or deleted files before
applying the candidate in the disposable publication repository.

## 9. Validate GitHub Actions wiring

```bash
make github-doctor
make actions-check
```

The doctor checks required installation files, pinned actions, Docker access,
and documented controller limits. The Action tests confirm that the composite
Action and workflow:

- default `SAGE_RUNTIME` to `v2`;
- default `SAGE_V2_ADMISSION_ENABLED` to `false`;
- require both OpenAI and Gemini credentials for the solve job;
- do not expose the removed `OPENAI_MODEL` setting; and
- keep external actions pinned and secret handling constrained.

## Expected artifacts

A model-backed V2 run writes controller-owned artifacts outside the candidate
repository. Depending on the route and outcome, these include:

- run metadata and terminal result;
- `usage.json` with model roles, attempts, and session counters;
- a versioned Solver plan and current-plan alias;
- the authoritative candidate diff and changed paths;
- verification results and bounded logs;
- independent review results;
- Admission context and evidence only when Admission ran; and
- research records only when configured research was used.

The repository diff, not a model-supplied patch field, is the authoritative
candidate.

## Troubleshooting

- `GEMINI_API_KEY is required for V2`: configure the Reviewer credential even
  when Admission is disabled.
- `SAGE_GOOGLE_MODEL_CONTEXT_APPROVED=true is required for V2`: explicitly
  acknowledge that the review packet may be sent to the configured Google
  model.
- Docker daemon or sandbox image errors: run `make sandbox-build`, then
  `make sandbox-smoke`.
- Admission unexpectedly ran: remove `SAGE_V2_ADMISSION_ENABLED=true` from the
  shell, `.env`, repository variables, or workflow inputs.
- No Admission context artifact: this is expected when Admission is disabled.
- Exit status 2 from `sage solve`: inspect the terminal outcome and artifacts;
  the run did not produce a publishable completed candidate.
