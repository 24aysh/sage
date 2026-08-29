# V2 Solver and Reviewer Testing Guide

## Purpose

This guide verifies the implemented V2 runtime: the tool-driven Solver,
deterministic verification, the independent Reviewer, and the bounded repair
and rereview loop. V2 is the only supported runtime; `SAGE_RUNTIME` can be
omitted or set to `v2`.

## Prerequisites

Run commands from the repository root. Install Git, `uv`, and Docker with a
reachable daemon, then prepare the project:

```bash
make env
make setup
make sandbox-build
make sandbox-smoke
```

For live model-backed tests, put these values in `.env` or export them in the
shell:

```dotenv
OPENAI_API_KEY=...
GEMINI_API_KEY=...
SAGE_GOOGLE_MODEL_CONTEXT_APPROVED=true
```

The OpenAI credential is used by the Solver and the Gemini credential by the
independent Reviewer. Do not commit `.env`.

## Offline verification

Run the V2-focused checks:

```bash
make v2-check
```

This runs the V2 runtime, workflow, provider, research, configuration, GitHub
integration, Action-policy, and compile checks without paid model calls.

Run the complete deterministic repository suite with:

```bash
make check
```

## Inspect the graphs

Print the per-session Solver LangGraph:

```bash
make graph
```

Validate and print the V2 orchestration topology:

```bash
make v2-graph
```

The Solver graph contains an agent node, tool node, finalization node, turn
limit, and invalid-response route. The Reviewer is a separate structured model
call, so it does not appear inside the Solver LangGraph.

## Run a live local solve

Choose a clean local Git repository and create an Issue Markdown file with a
bounded objective and acceptance criteria. Then run:

```bash
make first-run \
  REPO=/absolute/path/to/repository \
  ISSUE=/absolute/path/to/issue.md
```

The same execution path is available explicitly through:

```bash
make solve \
  REPO=/absolute/path/to/repository \
  ISSUE=/absolute/path/to/issue.md \
  BASE_REF=HEAD
```

Expected behavior:

1. configuration resolves to V2;
2. the Solver inspects the repository and saves a typed plan before mutation;
3. structured tools change files in an isolated worktree;
4. the controller derives changed paths and the candidate diff from Git;
5. deterministic checks run in the network-disabled sandbox;
6. Gemini independently reviews the bounded candidate packet; and
7. a successful result remains an uncommitted diff in the isolated run
   workspace.

The command prints the run directory. Inspect it with:

```bash
make run-status RUN_DIR=/absolute/path/to/run
```

Check `solver-plan.json`, `solver-final.json`, `verification-summary.json`,
`review.json`, `usage.json`, `changed-files.json`, `diff.patch`, and
`terminal.json` as applicable to the route.

## Verify Solver and Reviewer feedback cycles

The deterministic integration test covers this sequence:

```text
Solver -> Reviewer fail -> Solver repair -> Reviewer fail
       -> Solver repair -> Reviewer pass
```

Run it directly with:

```bash
uv run --project apps/agent pytest \
  -c apps/agent/pyproject.toml \
  apps/agent/tests/runtimes/v2/test_runtime.py
```

The test confirms that every repair starts a fresh Solver session, every
candidate is verified before review, and every repaired candidate receives a
fresh review. It also confirms that feedback is passed through controller-built
packets rather than shared agent messages.

For verifier-driven repair and workflow candidate invariants, run:

```bash
uv run --project apps/agent pytest \
  -c apps/agent/pyproject.toml \
  apps/agent/tests/runtimes/v2/test_runtime.py \
  apps/agent/tests/workflow/test_solve.py
```

## Verify configuration

Run:

```bash
uv run --project apps/agent pytest \
  -c apps/agent/pyproject.toml \
  apps/agent/tests/test_config.py
```

The tests confirm that omitted or blank `SAGE_RUNTIME` resolves to `v2`, old
runtime names are rejected, both provider credentials are required for live V2
configuration, and the configured Solver/Reviewer model values are honored.

These commands must fail before solving:

```bash
SAGE_RUNTIME=v1 make solve \
  REPO=/absolute/path/to/repository ISSUE=/absolute/path/to/issue.md

SAGE_RUNTIME=v2-prototype make solve \
  REPO=/absolute/path/to/repository ISSUE=/absolute/path/to/issue.md
```

## Test GitHub publication without model calls

Exercise branch creation, commit creation, and draft-pull-request intent with:

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

The smoke test reports zero model and network calls. It also validates created
and deleted file patches before applying the candidate in a disposable
publication checkout.

## Validate GitHub Actions wiring

```bash
make github-doctor
make actions-check
```

These checks confirm that the workflow defaults to V2, requires both model
credentials for the solve job, keeps external actions pinned, scopes secrets,
and uploads only allowlisted diagnostics.

## Troubleshooting

- `GEMINI_API_KEY is required for V2`: configure the Reviewer credential.
- `SAGE_GOOGLE_MODEL_CONTEXT_APPROVED=true is required for V2`: acknowledge
  that the bounded review packet may be sent to the configured Google model.
- Docker daemon or image errors: run `make sandbox-build`, then
  `make sandbox-smoke`.
- Exit status 2 from `sage solve`: inspect `terminal.json` and the run artifacts;
  the run did not produce a publishable completed candidate.
- Review keeps failing: inspect versioned `reviews/` entries, the latest plan,
  and verification summaries to distinguish repeated no-progress findings from
  a new repair cycle.
