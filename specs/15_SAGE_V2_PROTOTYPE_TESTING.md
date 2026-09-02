# Sage V2 Sequential Prototype Testing Guide

> **Status:** Historical guide for the removed Planner/patch-first prototype.
> Use [`../docs/testing.md`](../docs/testing.md) for current commands.

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
and data-use behavior depend on the account and service tier.
`SAGE_GOOGLE_MODEL_CONTEXT_APPROVED` defaults to `true`, so selecting V2
acknowledges that Issue and bounded repository context may be sent to the
configured Google model account. Set it explicitly to `false` to block V2
Google calls. This setting does not detect or change the account's terms; it is
a Sage privacy switch, not a Google API permission or OAuth scope.

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
- optionally, a LangSmith account and API key for hosted trace observability;
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
profile, or an explicit Google-context rejection before constructing the
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

## Observe V2 agents in LangSmith

LangSmith tracing is optional and disabled by default. Enabling it sends the
Issue, selected repository context, model inputs, and structured model outputs
to the configured LangSmith workspace. Enable it only for repositories whose
owners have approved that additional data transfer.

Add the following to the untracked `.env` file:

```dotenv
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=<langsmith-api-key>
LANGSMITH_PROJECT=sage-v2
# Set this only when the API key can access multiple workspaces.
LANGSMITH_WORKSPACE_ID=<workspace-id>
```

Run the normal workflow:

```bash
make v2-first-run \
  REPO=/absolute/path/to/repository \
  ISSUE=/absolute/path/to/issue.md
```

Open the `sage-v2` project in LangSmith and locate the trace named
`Sage V2 Workflow`. Its graph spans contain named model spans for the roles
that actually ran:

```text
Sage V2 Workflow
├── Planner
├── Solver
└── Reviewer
```

Repair and retry calls retain the same role name and are distinguishable by
the `sage_stage`, `sage_attempt`, `sage_call_number`, provider, model, and local
`sage_run_id` metadata. Use `sage_run_id` to correlate the hosted trace with
`.sage/runs/<run-id>/usage.json`.

The terminal simultaneously prints privacy-safe activity panels such as:

```text
Planner: activity
  ├─ Task: Assess issue readiness and draft the execution plan
  ├─ Stage: intake-planner
  ├─ Attempt: primary
  ├─ Model: google/gemini-3.5-flash
  └─ Call: 1/6
```

Result panels show structured decisions and counts, but never prompt text,
repository content, generated patches, review evidence, or credentials. To keep
payloads out of hosted traces while retaining names, timing, tags, and metadata,
set one or both of these standard LangSmith controls:

```dotenv
LANGSMITH_HIDE_INPUTS=true
LANGSMITH_HIDE_OUTPUTS=true
```

With either payload hidden, use the terminal activity panels and local run
artifacts for content-level diagnosis. Sage flushes pending LangSmith traces
before the CLI exits; a trace-upload failure is logged but cannot change the
repository result.

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

Once both provider keys are present in `.env`, choose a committed test
repository and a complete Issue file. Run from the repository root:

```bash
make v2-first-run \
  REPO=/absolute/path/to/committed/repository \
  ISSUE=/absolute/path/to/issue.md \
  BASE_REF=HEAD
```

The target performs setup, rebuilds and smoke-tests the network-disabled
sandbox, runs the deterministic V2 checks, and solves the requested Issue with
the constrained cross-provider profile. It then validates the retained
candidate and artifacts under `.sage/runs/`.

Unlike the general `make solve` wrapper, this smoke target is strict: a safe
clarification, blocked, no-change, or other non-publishable terminal outcome
causes the command to fail. Success therefore means the local V2 workflow
produced a non-empty, Git-authoritative candidate and passed artifact checks.

Provide `REPO` and `ISSUE` together. The target retains
`SAGE_VERIFICATION_COMMANDS_JSON` from trusted local configuration.

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
2. Leave repository variable `SAGE_RUNTIME` unset to use the workflow's
   `v2-prototype` default, or set it explicitly to `v2-prototype`. A stale
   value of `v1` deliberately selects the legacy runtime and legacy logs.
3. Google context approval defaults to `true`. After reviewing the data-use
   warning above, either leave `SAGE_GOOGLE_MODEL_CONTEXT_APPROVED` unset or set
   it to `true`; set it to `false` to block V2 Google calls.
4. Optionally set `SAGE_V2_PLANNER_MODEL`,
   `SAGE_V2_PLANNER_FALLBACK_MODEL`, `SAGE_V2_SOLVER_MODEL`, and
   `SAGE_V2_REVIEWER_MODEL`; otherwise the documented defaults are used.
5. To enable hosted observability, add repository secret `LANGSMITH_API_KEY`,
   set `LANGSMITH_TRACING=true`, and optionally set `LANGSMITH_PROJECT` and
   `LANGSMITH_WORKSPACE_ID`. Leave tracing false when repository context must
   not be sent to LangSmith. `LANGSMITH_HIDE_INPUTS` and
   `LANGSMITH_HIDE_OUTPUTS` are also supported as repository variables.
6. Ensure the installed Sage action references the implementation's full
   40-character pinned commit SHA.
7. Open one small, explicit Issue and post exactly `/sage solve` from a user
   with write or admin permission.
8. Confirm the gate accepts one exact base SHA and the solve checks out that
   SHA with persisted credentials disabled.
9. Confirm any created branch is `sage/issue-<number>` and the Pull Request is
   draft. Sage must never merge it or mark it ready automatically.
10. Download the seven-day diagnostics artifact and confirm it contains only
   the allowlisted summaries—not contexts, full logs, the workspace, or keys.
11. Review `usage.json`, `terminal.json`, the verification summary, and the draft
   diff before starting another canary.

### Confirm V2 logs and publication integrity

Open the `Solve and publish the authorized Issue` step in the Actions run. A V2
run starts with `V2 workflow: started` and then prints the same privacy-safe
panels as `make v2-first-run`, including `Planner: activity`, `Solver: activity`,
and `Reviewer: activity` when routing reaches each role. Lines from
`sage.runtimes.langgraph` instead mean repository variable `SAGE_RUNTIME` is
still set to `v1`; remove it or change it to `v2-prototype` before invoking a
new exact command comment.

For a project whose verification installs dependencies or writes build output,
confirm the resulting draft PR contains only the Issue implementation. In
particular, generated untracked paths such as `node_modules/`, `.venv/`,
`__pycache__/`, `dist/`, and `build/` must not appear in the commit. These paths
are outside the authoritative candidate boundary even when the target
repository does not ignore them. Tracked source files under a directory with
one of those names remain eligible when the Issue intentionally modifies them.

The deterministic regression can be rerun without API keys:

```bash
uv run --frozen --project apps/agent pytest -q \
  apps/agent/tests/integrations/github/test_publishing.py \
  apps/agent/tests/repository/test_git.py \
  apps/agent/tests/repository/test_host_git.py
```

It creates a candidate with untracked `node_modules` output and verifies that
publication succeeds without committing that output. It also retains coverage
for additions, deletions, renames, and binary patches.

The gate and finalizer jobs receive no model key. The checkout, dependency
install, Docker build, Docker container, and artifact-upload steps also receive
no model key. Both model keys are scoped only to the trusted controller solve
step; repository commands execute in the network-disabled container without
those values. The optional LangSmith key is likewise scoped only to the solve
step. LangSmith traces are a separate hosted observability channel and are not
part of the seven-day Actions diagnostics artifact.

## Offline GitHub publication smoke test

Use this before another `/sage solve` canary when solving and verification are
already known to work. It exercises the production publication function with a
real local candidate checkout and a local bare Git remote. A small in-memory
GitHub client records the draft Pull Request request. It makes zero model calls,
zero GitHub API calls, and zero network calls.

Run the built-in fixture:

```bash
make v2-github-smoke
```

A successful run reports the unchanged `main` SHA, the new
`sage/issue-17` SHA, the deterministic commit subject, and
`Draft PR requested: true`. The retained checkout and bare remote are placed
under `.sage/publication-smoke/<run-id>/`; no existing directory is overwritten.

To replay a saved `diff.patch` against a local clone of the target repository:

```bash
make v2-github-smoke \
  REPO=/absolute/path/to/testing-sage \
  PATCH=/absolute/path/to/artifact/diff.patch \
  BASE_REF=<artifact-base-sha> \
  ISSUE_NUMBER=5
```

Read `base_sha` from the downloaded artifact's `metadata.json`. The command
clones that exact commit into its own retained output, applies the patch using
the same deterministic normalization as V2, and invokes the production
creation-only publisher. It does not modify the supplied repository. To select
an explicit retained location, add `OUTPUT_DIR=/absolute/new/path`; that path
must not already exist.

Inspect the simulated remote directly when needed:

```bash
git --git-dir .sage/publication-smoke/<run-id>/remote.git \
  log --oneline --decorate --all
git --git-dir .sage/publication-smoke/<run-id>/remote.git \
  diff main sage/issue-17
```

This test proves local Git validation, authoritative staging, commit creation,
creation-only branch push, unchanged default branch, and the draft-PR request.
It intentionally does not test GitHub permissions, branch protection, REST API
availability, or Actions token configuration; those still require one
controlled canary after the offline test passes.

## Failure guide

| Symptom | Meaning and recovery |
| --- | --- |
| Configuration rejects a missing key | Add the missing repository secret or switch to V1. Never post it in the Issue. |
| Google context approval is false | Review account/data-use suitability, then remove the override or set it to true only when Google context use is acceptable. |
| LangSmith trace is missing | Confirm `LANGSMITH_TRACING=true`, `LANGSMITH_API_KEY` is set, the project/workspace values select the intended workspace, and the run reached a traced graph or model call. |
| LangSmith shows the workflow but no role name | Search the trace spans for `Planner`, `Solver`, or `Reviewer`; only roles reached by deterministic routing are invoked. |
| Authentication/model access | Replace or authorize only the affected provider credential/model, then create one fresh invocation. |
| Quota exhausted | Restore billing/usage capacity; an immediate retry is unlikely to help. |
| Rate limited | Check safe `retry_after`/attempt metadata, wait for the window, and avoid repeated commands. |
| `invalid_model_output` | The role failed its structured schema and one bounded schema repair did not recover. Inspect `usage.json`; do not edit the schema artifact. |
| `budget_exhausted` | Six calls or the finalization time reserve was reached. Narrow the Issue rather than raising limits casually. |
| `verification_failed` | Read the `Verifier: failure` line, then inspect `verification-summary.json` and its referenced log. Required checks never reach publication. `new blank line at EOF` is normalized automatically by revisions containing the whitespace fix. |
| `review_failed` | Inspect criterion results and blocking findings. A second review repair is not permitted. |
| Clarification repeats | Answer all blocking questions explicitly; after round two, rewrite the Issue with a complete design. |
| Candidate diff changed after solve | Confirm the workflow pins a Sage revision containing the authoritative publication fix. Older publishers re-added generated dependency/build output after solve; the corrected publisher stages only the validated patch. |
| Patch reports `dev/null: No such file` | Pin a revision with pair-aware patch-header normalization. Sage canonicalizes bare, quoted, and Git-prefixed aliases such as `dev/null`, `"a/dev/null"`, and `b/dev/null` to unified diff's required `/dev/null`. It preserves a real repository path named `dev/null`. |
| Patch reports `corrupt patch at line ...` | Pin a revision with deterministic Git recount support. Sage ignores inaccurate model-supplied hunk counts and lets Git recount the actual hunk lines while still requiring context to match. |
| No Pull Request | Check `terminal.json`; every non-`completed` outcome is deliberately non-publishable. |

### Patch application diagnostics

For every Solver candidate, logs now show privacy-safe application metadata:

```text
Patch: applying files=2 lines=18 digest=4b2f... recount=true whitespace=fix
Patch: whitespace normalized digest=4b2f... detail=... new blank line at EOF ...
Patch: finished status=applied digest=4b2f...
```

The digest correlates the start and finish lines without printing repository
content or the model patch. A rejected patch prints Git's bounded reason. The
initial candidate and its one allowed implementation repair have different
digests, which makes it clear whether the Solver returned a genuinely revised
patch.

Sage safely normalizes CRLF line endings and unambiguous null-file header
variants. Models sometimes emit `a/dev/null` or `b/dev/null`; Git strips the
Git prefix and then tries to open a real `dev/null` path. Pair-aware
normalization converts these variants only when the opposite `---`/`+++`
header names a different file, so a legitimate repository file named
`dev/null` remains usable. A correction appears as
`Patch: normalized null file headers count=1` without printing patch content.

Sage also invokes `git apply --recount --whitespace=fix`, which corrects
inaccurate numbers in `@@` hunk headers and Git-recognized whitespace errors
such as a new blank line at EOF. Git still rejects missing context, wrong source
content, unsafe paths, Git-internal paths, malformed file headers, and patches
that do not apply to the exact workspace.

Run the deterministic regression without provider keys:

```bash
uv run --frozen --project apps/agent pytest -q \
  apps/agent/tests/repository/test_patch.py \
  apps/agent/tests/runtimes/v2/test_validation.py \
  apps/agent/tests/test_makefile.py
```

The tests apply real patches to temporary Git repositories, including new-file
patches with `--- dev/null`, `--- a/dev/null`, and a quoted alias; deletion via
`+++ b/dev/null`; an actual tracked `dev/null` path; a trailing blank line; and
deliberately inaccurate hunk counts. The Makefile checks also verify that
`make v2-first-run` disables Git's pager for its final diff summary, so local
testing cannot open an interactive `less` screen. If a pager is already open,
press `q`; it did not alter the candidate.

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
