# Sage V2 Sequential Prototype Testing Guide

## Purpose

This guide validates the first Sage V2 prototype from a maintainer's point of
view. V2 keeps the existing exact-SHA clone, network-disabled Docker sandbox,
Git-derived diff, creation-only branch, and draft Pull Request boundaries. It
replaces the solve runtime with one sequential route:

```text
Gemini 3.5 Flash Planner -> GPT-5.4 mini Solver -> hard verification -> Gemini 3.5 Flash Reviewer
```

The normal ready path uses three model calls. The graph can make one bounded
readiness-context expansion, one Solver-context expansion, one implementation
repair, and one review repair. Every outbound attempt—including retry,
fallback, and schema repair—counts toward the hard six-call ceiling.

The prototype deliberately does not include parallel workers, worker clones,
merge agents, replanning, graph resume, long-term memory, automatic merge, or
promotion from draft to ready for review. V1 remains the default and rollback
runtime.

## Cost, privacy, and suitability warning

OpenAI API calls may be billed to the configured project. Gemini account terms
and data-use behavior depend on the account and service tier. Before V2 will run,
a repository owner must explicitly set
`SAGE_GOOGLE_MODEL_CONTEXT_APPROVED=true`. That setting acknowledges that Issue
and bounded repository context may be sent to the configured Google model
account; it does not detect or change the account's terms. It is a Sage privacy
gate, not a Google API permission or OAuth scope.

Do not enable this profile for repositories whose source, Issue text, or
discussion is not permitted to be sent to both configured providers. Do
not paste credentials into Issues, comments, shell commands, logs, fixtures, or
committed files.

## Prerequisites

Have the following before a live test:

- Linux with Git, Docker, Python 3.14, and `uv`;
- access to `google/gemini-3.5-flash` and its listed Planner fallback;
- access to `openai/gpt-5.4-mini`;
- one API key for Google and OpenAI;
- a small disposable Git repository for local live tests; and
- repository admin access for a GitHub Actions canary.

The normal offline suite uses fake providers and makes no paid API call.

## Configure V2 locally

Create the local untracked configuration once:

```bash
make env
```

Edit `.env` and use placeholder-shaped values only in examples or shared
screenshots:

```dotenv
GEMINI_API_KEY=<google-api-key>
OPENAI_API_KEY=<openai-api-key>

SAGE_RUNTIME=v2-prototype
SAGE_MODEL_PROFILE=constrained-cross-provider
SAGE_GOOGLE_MODEL_CONTEXT_APPROVED=true
SAGE_V2_PLANNER_MODEL=gemini-3.5-flash
SAGE_V2_PLANNER_FALLBACK_MODEL=gemini-3.5-flash-lite
SAGE_V2_SOLVER_MODEL=gpt-5.4-mini
SAGE_V2_REVIEWER_MODEL=gemini-3.5-flash

SAGE_SANDBOX_IMAGE=sage-sandbox:v0
SAGE_VERIFICATION_COMMANDS_JSON=[]
```

`.env` must remain untracked. V2 rejects a missing credential, another model
profile, or a missing Google-context acknowledgement before constructing the
provider set.

Optional trusted verification commands are a JSON list. They run only inside
the network-disabled repository sandbox and are capped at three entries:

```dotenv
SAGE_VERIFICATION_COMMANDS_JSON=[{"id":"focused","command":"pytest -q tests/test_widget.py","required":true,"timeout_seconds":120}]
```

Only repository owners should configure these commands. Planner and Solver
command suggestions still pass through a conservative allowlist. Failed
optional checks remain visible in the verification summary and terminal
uncertainty, but only required check failures or timeouts trigger repair or
block completion.

## Run all offline checks first

Install the locked environment and build the sandbox:

```bash
make setup
make sandbox-build
make sandbox-smoke
```

Run the V2-specific deterministic suite before spending on a live call:

```bash
make v2-check
```

This covers provider policy, attempt accounting, repository scope safety,
context bounds, artifacts, hard verification, sequential runtime paths,
clarification/status behavior, Actions secret policy, and Python compilation.
It does not contact Google, OpenAI, or GitHub.

Also keep the rollback baseline green:

```bash
make v1-check
```

## Inspect the sequential graph

Print the V2 Mermaid graph:

```bash
make v2-graph
```

Confirm it has Planner, Solver, hard-verification, Reviewer, repair, and
terminal nodes. It must not contain worker dispatch, `Send`, merge-agent,
parallel, or replanning nodes.

## Live test 1: ready three-call Issue

The repository includes a ready-to-run version of this smoke test. Once the
both keys and `SAGE_GOOGLE_MODEL_CONTEXT_APPROVED=true` are present in `.env`,
run from the repository root:

```bash
make v2-first-run
```

The target performs setup, rebuilds and smoke-tests the network-disabled
sandbox, runs the deterministic V2 checks, copies `v2-manual-test/project` into
a temporary Git repository, and solves `v2-manual-test/issue.md`. It forces the
constrained cross-provider profile and a required
`python3 calculator_checks.py` verification command for this fixture. It
then validates the retained candidate and artifacts under `.sage/runs/`.

Unlike the general `make solve` wrapper, this smoke target is strict: a safe
clarification, blocked, no-change, or other non-publishable terminal outcome
causes the command to fail. Success therefore means the local V2 workflow
produced a non-empty, Git-authoritative candidate and passed artifact checks.

The checked-in fixture is never modified. Its initial failing tests are
intentional and reproduce the one-character bug described by the Issue.

The same strict command accepts a custom committed repository and Issue, like
the V1 `first-run` target:

```bash
make v2-first-run \
  REPO=/absolute/path/to/committed/repository \
  ISSUE=/absolute/path/to/issue.md \
  BASE_REF=HEAD
```

Provide `REPO` and `ISSUE` together. If both are omitted, the checked-in sample
is used. Custom runs retain `SAGE_VERIFICATION_COMMANDS_JSON` from trusted local
configuration; only the sample run forces its fixture-specific check.

Live CLI output includes progress lines for every model attempt:

```text
Planner: started stage=intake-planner call=1 attempt=primary provider=google ...
Planner: finished stage=intake-planner call=1 ... outcome=success ...
Solver: started stage=solver call=2 attempt=primary provider=openai ...
Verifier: finished pass=1 status=pass ...
Reviewer: started stage=review call=3 attempt=primary provider=google ...
```

Retries, fallbacks, schema repairs, and rereviews appear as separate counted
attempts. Logs contain operational metadata only; Issue text, repository
context, model responses, patches, verification output, and credentials remain
in their bounded artifact locations and are not printed as activity messages.

### Equivalent custom disposable repository

Use a disposable repository with a committed `app.py` containing
`value = 1`. Save this Issue text outside that repository as
`/tmp/sage-v2-ready.md`:

```markdown
# Set the sample value to 2

Change `app.py` so the top-level `value` is `2` instead of `1`.

Acceptance criteria:

- `app.py` contains `value = 2`.
- `git diff --check HEAD --` passes.
- Do not change any other file.
```

Run:

```bash
make solve REPO=/absolute/path/to/disposable-repo ISSUE=/tmp/sage-v2-ready.md
```

Expected result:

- terminal outcome `completed`;
- one Google Planner call, one OpenAI Solver call, and one Google Reviewer
  call;
- `usage.json` contains exactly three successful attempts;
- the candidate has only `app.py` changed; and
- no commit, push, or Pull Request is created by the local command.

Model output is nondeterministic, so treat deviations as test observations,
not permission to rerun repeatedly without checking cost and failure evidence.

## Live test 2: under-specified Issue

Save this as `/tmp/sage-v2-vague.md`:

```markdown
# Improve the sample value

Change the value in `app.py` so it is better.
```

Run the same `make solve` command with that Issue. Expected result:

- terminal outcome `needs_human_information` or
  `needs_human_design_decision`;
- one Gemini Planner attempt;
- no Solver or Reviewer attempt;
- no candidate diff; and
- one consolidated packet containing at most three blocking questions.

For a GitHub run, answer every question in one new human comment. Then create a
new comment containing exactly `/sage solve`. Sage starts a fresh run; it does
not wait or resume the old job. The newest clarification and replies after it
are included in the new bounded Issue context. After two clarification rounds,
an Issue that still cannot be admitted ends `needs_maintainer_rewrite`.

## Live test 3: repository-context expansion

Create a small fixture where the Issue names an identifier but not its file,
and where one exact repository search resolves it. For example, place
`DEFAULT_WIDGET_LIMIT` in one tracked config module and ask Sage to update that
constant without naming the module.

Expected `usage.json` sequence:

```text
planner -> planner readiness recheck -> solver -> reviewer
```

Expected provenance is four calls and
`readiness_context_expansions: 1`. A second readiness-context request is not
allowed and must terminate safely.

## Live test 4: verification repair

This path can incur an additional OpenAI call. Use a tiny disposable fixture
and a trusted focused verification command that reliably catches an initially
plausible wrong value. Do not induce repeated paid failures in a production
repository.

Expected successful sequence:

```text
planner -> solver -> solver implementation repair -> reviewer
```

Confirm `implementation_repairs` is exactly `1`, both verification passes have
separate directories, and the Reviewer saw only the final hard-verified diff.
A repeated failure or unchanged failure fingerprint must stop; it cannot start
a second implementation repair.

## Inspect a completed run

The CLI prints the run directory. Start with the existing safe summary:

```bash
make run-status RUN_DIR=/absolute/path/to/.sage/runs/<run-id>
```

For V2, inspect these bounded JSON artifacts locally:

```text
repository-map.json       deterministic repository inventory and excerpts
intake.json               admission decision and readiness dimensions
plan.json                 proposed sequential plan and acceptance criteria
autonomy-contract.json    frozen scope, assumptions, criteria, and budgets
usage.json                every counted provider attempt and actual fallback
verification-summary.json latest hard-verification summary and fingerprints
review.json               read-only criterion review and findings
terminal.json             typed terminal outcome
agent-final.json          workflow-compatible final result
changed-files.json        Git-authoritative changed paths
diff.patch                Git-authoritative candidate diff
```

`contexts/`, `proposals/`, and per-pass verification logs are local diagnostics
and are intentionally excluded from the Actions upload allowlist.

To confirm provider/model provenance, inspect `usage.json`. The normal rows are:

| Role | Provider | Model |
| --- | --- | --- |
| Planner | `google` | `gemini-3.5-flash` |
| Solver | `openai` | `gpt-5.4-mini` |
| Reviewer | `google` | `gemini-3.5-flash` |

Planner fallback must say `google/gemini-3.5-flash-lite`. Solver and Reviewer
have no fallback. The attempt kind must disclose `primary`, `retry`, `fallback`,
or `schema_repair`. Configured overrides must appear truthfully in `usage.json`.

## CLI exit codes

The direct `sage solve` command uses:

- `0`: a completed, publishable, non-empty candidate;
- `1`: configuration, controller, repository, sandbox, or runtime execution
  failed; and
- `2`: a safe terminal result with no publishable diff, including
  clarification, no-change, blocked, unsupported, provider, budget,
  verification, and review outcomes.

The Makefile's `make solve` wrapper prints exit code `2` as a warning and exits
successfully so maintainers can inspect the safe terminal artifacts.

## Controlled GitHub Actions canary

Use a non-sensitive disposable repository or branch policy first.

1. Add repository secrets `GEMINI_API_KEY` and `OPENAI_API_KEY`.
2. Set repository variable `SAGE_RUNTIME` to `v2-prototype`.
3. Set `SAGE_GOOGLE_MODEL_CONTEXT_APPROVED` to `true` only after the repository
   owner approves Google context use.
4. Optionally set `SAGE_V2_PLANNER_MODEL`,
   `SAGE_V2_PLANNER_FALLBACK_MODEL`, `SAGE_V2_SOLVER_MODEL`, and
   `SAGE_V2_REVIEWER_MODEL`; otherwise the documented defaults are used.
5. Ensure the installed Sage action references the implementation's full
   40-character pinned commit SHA.
6. Open one small, explicit Issue and post exactly `/sage solve` from a user
   with write or admin permission.
7. Confirm the gate accepts one exact base SHA and the solve checks out that
   SHA with persisted credentials disabled.
8. Confirm any created branch is `sage/issue-<number>` and the Pull Request is
   draft. Sage must never merge it or mark it ready automatically.
9. Download the seven-day diagnostics artifact and confirm it contains only
   the allowlisted summaries—not contexts, full logs, the workspace, or keys.
10. Review `usage.json`, `terminal.json`, the verification summary, and the draft
   diff before starting another canary.

The gate and finalizer jobs receive no model key. The checkout, dependency
install, Docker build, Docker container, and artifact-upload steps also receive
no model key. Both model keys are scoped only to the trusted controller solve
step; repository commands execute in the network-disabled container without
those values.

## Failure guide

| Symptom | Meaning and recovery |
| --- | --- |
| Configuration rejects a missing key | Add the missing repository secret or switch to V1. Never post it in the Issue. |
| Google acknowledgement missing | Review account/data-use suitability, then explicitly approve or do not use V2. |
| Authentication/model access | Replace or authorize only the affected provider credential/model, then create one fresh invocation. |
| Quota exhausted | Restore billing/usage capacity; an immediate retry is unlikely to help. |
| Rate limited | Check safe `retry_after`/attempt metadata, wait for the window, and avoid repeated commands. |
| `invalid_model_output` | The role failed its structured schema and one bounded schema repair did not recover. Inspect `usage.json`; do not edit the schema artifact. |
| `budget_exhausted` | Six calls or the finalization time reserve was reached. Narrow the Issue rather than raising limits casually. |
| `verification_failed` | Inspect `verification-summary.json` and the corresponding local log. Required checks never reach publication. |
| `review_failed` | Inspect criterion results and blocking findings. A second review repair is not permitted. |
| Clarification repeats | Answer all blocking questions explicitly; after round two, rewrite the Issue with a complete design. |
| No Pull Request | Check `terminal.json`; every non-`completed` outcome is deliberately non-publishable. |

### Gemini HTTP 400 during Planner or Reviewer calls

`gemini-3.5-flash` and `gemini-3.5-flash-lite` support structured output, but
Google accepts only a subset of JSON Schema and may reject deeply nested or
constraint-heavy schemas with HTTP 400 `INVALID_ARGUMENT`. Sage sends Google a
compact structural schema and then applies the complete Pydantic contract
locally. If local validation fails, the one bounded schema-repair call receives
only field paths and validation types; repository content and invalid values
are not copied into the repair diagnostic.

Check `usage.json` for `status_code`, `request_id`, `error_category`, and the
attempt sequence. A schema repair appears as a separate counted
`schema_repair` attempt. Repeated HTTP 400 responses indicate a provider SDK or
schema compatibility regression, not a missing model, when the configured key
can list the model.

### Python 3.14 Google SDK warning

The Google GenAI SDK currently imports Python's deprecated private
`_UnionGenericAlias` compatibility type. This does not affect execution on
Python 3.14. Sage suppresses only that exact third-party deprecation warning in
pytest; other warnings remain visible. Revisit the narrow filter when upgrading
the Google SDK or before moving to Python 3.17.

## Immediate rollback

For local use, set:

```dotenv
SAGE_RUNTIME=v1
```

For GitHub, set repository variable `SAGE_RUNTIME` to `v1`. V1 needs only the
OpenAI key and retains its existing behavior. Rollback does not require
deleting V2 artifacts, Issue clarification comments, or branches. Do not
delete a Sage branch automatically; publication remains creation-only.

## Results worksheet

Record one row per intentional canary:

| Date/run | Fixture | Base SHA | Calls | Providers/models | Input/output tokens | Latency | Context expansions | Implementation repairs | Review repairs | Terminal | Draft branch/PR | Observations |
| --- | --- | --- | ---: | --- | --- | --- | ---: | ---: | ---: | --- | --- | --- |
|  | ready |  |  |  |  |  |  |  |  |  |  |  |
|  | vague |  |  |  |  |  |  |  |  |  |  |  |
|  | context expansion |  |  |  |  |  |  |  |  |  |  |  |
|  | verification repair |  |  |  |  |  |  |  |  |  |  |  |

Stop the canary if a secret appears in output, a seventh attempt starts, a
non-publishable outcome reaches publication, the container has network access,
or the PR is not draft. Treat any of those as a controller defect rather than
a model-quality issue.
