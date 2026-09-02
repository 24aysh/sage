# Sage V2 Tool-Driven Migration Testing Guide

> **Status:** Historical migration guide. Its old runtime selectors and
> compatibility checks no longer apply. Use
> [`../docs/testing.md`](../docs/testing.md) for current commands and expected
> behavior.

This guide validates the architecture implemented from
[`16_SAGE_V2_ARCHITECTURE_MIGRATION.md`](16_SAGE_V2_ARCHITECTURE_MIGRATION.md):

```text
V2 = tool-driven Solver + saved Solver plan + independent Reviewer
```

The checks avoid paid model calls until the final local manual solve. V1
remains available with `SAGE_RUNTIME=v1`.

## 1. Prerequisites and configuration

From the Sage repository root, confirm that Git, Docker, and `uv` are installed
and that Docker is running:

```bash
git --version
docker info
uv --version
make env
```

Set these values in `.env`:

```dotenv
OPENAI_API_KEY=<solver-key>
GEMINI_API_KEY=<reviewer-key>
SAGE_RUNTIME=v2-prototype
SAGE_V2_SOLVER_MODEL=gpt-5.4-mini
SAGE_V2_REVIEWER_MODEL=gemini-3.5-flash
SAGE_GOOGLE_MODEL_CONTEXT_APPROVED=true
```

Do not configure `SAGE_V2_PLANNER_MODEL`, a Planner fallback, or
`SAGE_MAX_MODEL_CALLS`. V2 rejects those obsolete patch-first settings.

## 2. Run all offline checks

No provider call is made by this command:

```bash
make v2-check
```

Expected coverage includes:

- exact replace, create, replace, delete, move, traversal, and symlink tests;
- deterministic rejection of mutation before `save_plan`;
- multiple Solver/Reviewer repair cycles;
- more than six recorded calls without a global-call-budget error;
- unchanged V1 tool-loop behavior; and
- composite Action and installable workflow policy.

Run only the migration tests with:

```bash
uv run --project apps/agent pytest \
  apps/agent/tests/runtimes/v2 \
  apps/agent/tests/repository/test_edits.py \
  apps/agent/tests/providers/test_manager.py \
  apps/agent/tests/test_config.py
```

## 3. Inspect the role boundary offline

Run:

```bash
rg -n "PLANNER|SAGE_V2_PLANNER|max_model_calls|SAGE_MAX_MODEL_CALLS" \
  apps/agent/src .github/actions .github/workflows .env.example
```

The only expected matches are configuration migration checks that reject
obsolete environment names. There must be no Planner provider, prompt, graph
node, Action input, or workflow variable.

Confirm V2 does not register the raw patch tool:

```bash
uv run --project apps/agent pytest -q \
  apps/agent/tests/runtimes/v2/test_tools.py \
  apps/agent/tests/runtimes/v2/test_runtime.py
```

V1 intentionally retains `apply_patch` as its rollback-compatible tool.

## 4. Run one local end-to-end solve

Use a committed repository and an Issue file. This command runs the configured
models in Docker and checks the completed artifacts:

```bash
make v2-first-run \
  REPO=/absolute/path/to/repository \
  ISSUE=/absolute/path/to/issue.md
```

Both `REPO` and `ISSUE` are required. Expected logs include:

```text
Solver: activity
Solver: result
Verifier: started
Verifier: finished
Reviewer: activity
Reviewer: result
V2 workflow: terminal outcome=completed
```

There must be no `Planner:` activity. Call numbers are monotonic observations,
not a `/6` budget. A Reviewer failure may produce `solver-repair`, followed by
a fresh `rereview`.

## 5. Inspect artifacts

The command prints the run directory. Inspect it with:

```bash
make run-status RUN_DIR=/absolute/path/printed/by/the/run
find /absolute/path/printed/by/the/run -maxdepth 2 -type f | sort
```

For a completed V2 run, expect:

```text
solver-plan.json
solver-plans/01.json
solver-final.json
candidate-snapshot.json
verification-summary.json
review.json
reviews/01.json
usage.json
terminal.json
agent-final.json
changed-files.json
diff.patch
```

Verify that the plan is outside the target repository, `solver-final.json` has
no patch field, `diff.patch` came from Git, `review.json` passed, and
`usage.json` contains only Solver and Reviewer roles.

## 6. Test publication without provider calls

After a local solve produces a candidate patch, exercise the GitHub-like
publication boundary offline:

```bash
make v2-github-smoke \
  REPO=/absolute/path/to/a/local/clone \
  PATCH=/absolute/path/to/run/diff.patch \
  BASE_REF=<accepted-base-sha>
```

This uses local Git remotes and a fake GitHub client. It verifies creation-only
branch publication, reviewed candidate identity, and draft PR behavior without
calling OpenAI, Gemini, LangSmith, or GitHub.

## 7. Optional LangSmith check

Enable tracing only when sending Issue and repository context is approved:

```dotenv
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=<key>
LANGSMITH_PROJECT=sage-v2
```

Run one small solve. LangSmith should show named Solver and Reviewer activity;
repairs appear as new Solver/Reviewer stages. No Planner role should appear.

## 8. GitHub canary

Only after offline and local checks pass:

1. Install the workflow from the final pinned Sage commit.
2. Configure `OPENAI_API_KEY` and `GEMINI_API_KEY` as repository secrets.
3. Optionally configure only `SAGE_V2_SOLVER_MODEL` and
   `SAGE_V2_REVIEWER_MODEL` as repository variables.
4. Open one small deterministic Issue and comment exactly `/sage solve`.

Success requires the actual candidate to pass verification and review, a
creation-only branch to be pushed, and a draft Pull Request to open.

## 9. Failure diagnosis

- A plan-gate error means the Solver tried to edit before `save_plan`; the tool
  error is returned to it for correction.
- `verification_failed`: inspect `verification-summary.json` and
  `verification/` logs.
- `review_failed`: inspect `review.json`; identical candidate/finding
  fingerprints stop safely.
- `invalid_model_output`: a role violated its typed terminal contract.
- `provider_unavailable` or `rate_limited`: inspect `usage.json`; nothing is
  published.
- `unresolved`: no stable reviewed Git candidate was established before the
  deadline reserve.

Rollback remains:

```dotenv
SAGE_RUNTIME=v1
```
