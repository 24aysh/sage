# Testing Sage

Run commands from the repository root. Start with the smallest relevant check,
then run the complete offline gate before handing off a refactor. Current
ownership and evidence contracts are in [architecture.md](architecture.md).

## Setup and complete gate

Requirements: Python 3.14, `uv`, and Git. Docker is needed only for sandbox and
live-solve checks; model credentials are needed only for live calls.

```bash
make setup
make check
```

Setup may download locked dependencies. Once installed, `make check` runs the
deterministic Python suite and package compilation without model calls, Docker
or network access. The suite includes Action policy, architectural boundaries,
SQLite migrations and real temporary local Qdrant with fake embeddings.
There is no separately configured formatter, linter or type-checking gate.

Direct equivalent:

```bash
LANGSMITH_TRACING=false uv run --project apps/agent \
  pytest -c apps/agent/pyproject.toml
uv run --project apps/agent python -m compileall -q apps/agent/src
```

The optional pinned-reference test skips when no reference checkout is supplied.
A skip is not a parity certification.

## Jev navigation experiments

Jev is off by default. Unit tests use scripted judgments and HTTP fixtures;
they need no TypeSafe key and do not establish live efficiency gains.

```bash
uv run --project apps/agent pytest \
  apps/agent/tests/providers/test_typesafe.py \
  apps/agent/tests/orchestration/test_navigation.py \
  apps/agent/tests/orchestration/test_navigation_evaluation.py \
  apps/agent/tests/legion_memory/test_navigation.py \
  apps/agent/tests/repository/test_search.py
```

For a local pilot, set `TYPESAFE_API_KEY` privately and use
`SAGE_JEV_NAVIGATION_MODE=shadow|on`. This opts into sending bounded Issue,
plan and source evidence to TypeSafe. Set `SAGE_JEV_NAVIGATION_POLICY` to
`excerpts` (unchanged schemas) or `actions` (optional exploration goal).
For actions, `SAGE_JEV_MAX_FOLLOWUP_ACTIONS=1|2` selects the bound. Existing
`make solve` and `make legion-solve` honor these settings; memory is optional.
Never put credentials in the candidate repository or sandbox.

### Console logs for solve and legion-solve

Set these in the `.env` loaded by Make (or your selected `ENV_FILE`):

```dotenv
SAGE_JEV_NAVIGATION_MODE=on
SAGE_JEV_NAVIGATION_POLICY=actions
SAGE_JEV_MAX_FOLLOWUP_ACTIONS=1
SAGE_JEV_READ_PROBABILITY_THRESHOLD=0.65
SAGE_JEV_SEARCH_PROBABILITY_THRESHOLD=0.75
SAGE_JEV_GRAPH_PROBABILITY_THRESHOLD=0.80
SAGE_JEV_ACTION_CONFIDENCE_THRESHOLD=0.50
SAGE_JEV_LOG_INPUT=true
SAGE_JEV_CAPTURE=false
```

Also configure `OPENAI_API_KEY`, `GEMINI_API_KEY` and `TYPESAFE_API_KEY` privately
in that file. Run commands from the Sage checkout, with Docker running and the
sandbox image available (`make bootstrap` for first-time setup; `make doctor`
to check an existing setup). These solve commands make paid model requests.
Put Jev settings in the loaded env file: Make sources it before solving, so its
values override conflicting variables exported in your shell.

Use a committed target repository and an Issue requiring source exploration,
such as tracing a function's callers and tests. Replace the absolute example
paths below. Keep the Issue file and memory database outside the target repository.

Without Legion Memory:

```bash
make solve REPO=/absolute/repo ISSUE=/absolute/issue.md BASE_REF=HEAD
```

For a tools-only benchmark baseline, use:

```bash
make solve-baseline REPO=/absolute/repo ISSUE=/absolute/issue.md BASE_REF=HEAD
```

`solve-baseline` loads the selected `ENV_FILE`, then forcibly sets Jev navigation
to `off` and never passes a memory database to the solve. This remains true even
if the env file enables Jev or the command receives `MEMORY`/`LEGION_SOLVE`
values. The Solver retains its ordinary repository and mutation tools; normal
verification and Reviewer behavior are unchanged. Use the same Issue, base
commit, models and other settings when comparing this baseline with `legion-solve`.

With Legion Memory, first build the graph and then run a memory-enabled solve:

```bash
make legion-memory REPO=/absolute/repo \
  MEMORY_FILE=/absolute/sage-memory/graph.sqlite3 EMBEDDINGS=off

make legion-solve REPO=/absolute/repo ISSUE=/absolute/issue.md \
  MEMORY=/absolute/sage-memory/graph.sqlite3 EMBEDDINGS=off BASE_REF=HEAD
```

`legion-memory` builds committed-source memory; it does **not** call Jev, even
when navigation mode is on. Jev runs during `solve` or `legion-solve` exploration.
The build target uses `MEMORY_FILE`; the solve target uses `MEMORY`. Disabling
embeddings here isolates Jev from embedding cost/network activity; it does not
disable the graph. Keep the same repository/commit between build and solve.

The `actions` policy needs the Solver to supply `exploration_goal` on a read or
search; without it, navigation logs a skip. Set `SAGE_JEV_MAX_FOLLOWUP_ACTIONS=2`
to test dependent steps, though Jev may hand back before the second step.
Alternatively, set `SAGE_JEV_NAVIGATION_POLICY=excerpts` to test search-result
ranking without changed tool arguments; it needs at least two eligible windows.
Neither policy guarantees a Jev call on every Issue or tool invocation.

Both solve commands log Jev activity at INFO without `--debug` or replay capture:

- `Jev request`: the complete bounded JSON input (`model`, `state`, `questions`),
  including candidate evidence and criteria. Input is logged before sending,
  so it is visible even if the request fails. Oversized rejected inputs are not logged.
- `Jev navigation`: run/session/sequence/step and request correlation, candidate
  count, decisions/selected IDs, stop reasons and exposed character counts.
  `input_tokens` and `output_tokens` are provider-reported counts after a valid
  response, not estimates. Failed/unavailable usage is `"unknown"`, not zero.
- `candidate_retrieval_ms` measures local shortlist construction;
  `latency_ms` measures the complete Jev decision attempt, including request
  construction, HTTP wait and validation; `retrieval_ms` measures the selected
  read/search/graph operation, including failed retrieval attempts.
  `structural_retrieval_ms` measures optional Legion enrichment.
  `navigation_ms` is the total enrichment time for a returned root-tool result,
  including those stages and logging/artifact overhead, but excluding the root
  read/search itself. These measurements overlap: do not add them together.

At the end, both commands print elapsed totals (example):

```text
Elapsed time totals:
  Solver (including Jev): 100.00 seconds
  Solver (without Jev): 98.50 seconds
  Jev (decisions only): 1.50 seconds
  Reviewer: 15.00 seconds
Total solve time: 125.50 seconds
```

Solver totals sum complete initial/repair sessions, including tools and waits.
Reviewer totals sum complete reviews/rereviews, including prompt preparation,
retry backoff, schema repair and validation. Jev totals sum all decision attempts,
including failures/timeouts, from `usage.json`'s `semantic_calls`. Solver without
Jev subtracts that decision time; candidate/evidence retrieval remains Solver
work. It is not an estimate of the runtime with Jev disabled. Jev is already
included in the Solver total: do not add both together. Preparation, independent
verification and cleanup explain why whole-solve time exceeds role totals.
Per-session/stage measurements are recorded in `usage.json`'s `agent_timings`.
An uninvoked role reports zero for newly measured runs; missing historical
measurements report `unavailable` rather than inferring wall time from API latency.

The final whole-solve total remains independent of the role subtotals.
This uses the same duration as `workflow-timing.json`: from just before reading
the Issue through workspace/sandbox preparation, optional memory setup, all
Solver/Jev/Reviewer calls, verification/repairs, result persistence and resource
cleanup. It excludes earlier CLI prerequisite checks, dependency installation,
and final terminal rendering. It is shown with Jev/memory on or off, including
returned no-change or unsuccessful outcomes; it is elapsed time, not a success
claim. Legacy results without timing show `unavailable`, not zero.

### Interrupting a solve with Ctrl-C

Run either `make solve` or `make legion-solve` as above. Once the run is
initialized, press **Ctrl-C once** while the Solver, Jev, or Reviewer is working.
The CLI prints `Solve interrupted` with the run/workspace paths, recorded token
and tool usage, and the same Solver/Jev/Reviewer timing breakdown. The last time
line is `Elapsed time at interruption`, measured when cancellation reaches the
workflow, before sandbox/memory cleanup. No candidate is declared completed,
verified, or unchanged. Wait for cleanup to finish; the CLI retains its nonzero
interruption exit (1), and Make reports a failed/interrupted command.

Token totals include known Solver, Reviewer **and Jev** input/output usage.
`Model calls` counts generative requests; `Jev calls` is separate. Cancelled
in-flight requests may never report usage: those counts remain unknown, the
summary marks incomplete totals, and the provider may still bill them. Cached
input tokens are a subset of input tokens and are not added twice. Memory
embedding usage, when available, remains in the separate Legion Memory section.

Inspect `interrupted.json` for the interruption-time snapshot and `usage.json`
for recorded calls and elapsed agent sessions. `workflow-timing.json` still
includes subsequent cleanup, so its duration can exceed the interruption time.
The partial snapshot deliberately omits candidate diff/changed-file inspection;
inspect the printed workspace separately if you need to recover unfinished work.

The deterministic CLI tests send a real SIGINT in isolated subprocesses with
fake model/sandbox boundaries; no paid call is needed. Cancellation during
synchronous work may not be processed until that operation yields. Interruption
before run initialization has no run summary; repeated Ctrl-C, forced termination
or SIGKILL can prevent reporting or cleanup. Other failures retain their existing
error handling rather than being labelled manual interruption.

Full input logging defaults to **on only in navigation mode `on`**. Logs can
contain private Issue text, plans and source; do not upload or share them without
review. Authorization headers are never included, the TypeSafe key is redacted
if present in input, and terminal controls are escaped. Other secrets embedded
in source are **not automatically detected**. Set `SAGE_JEV_LOG_INPUT=false`
to retain timing/token/status summaries without body logs, including at DEBUG.
`shadow` logs summaries only; `off` constructs no Jev client and emits no Jev logs.
No eligible goal/candidates or exhausted budgets produce skip reasons rather
than a request; do not expect input/token logs for calls that never happen.
Timing events also appear in `navigation.json`; full request/response artifact
capture remains independently controlled by `SAGE_JEV_CAPTURE`.
For paired latency comparisons keep logging settings and output sinks identical;
terminal/file output itself can add overhead.

### Budgets, captures and evaluation

Time settings may lower, but not exceed, two seconds per request/eight seconds
total Jev wait. Failures preserve ordinary tool results. Inspect `usage.json`
(`semantic_calls`), `navigation.json`, and `workflow-timing.json`. Provisional
Score acceptance is 2/3 with confidence 0.5 and is not configured by the action
variables below.

For `policy=actions`, Sage dispatches only when the selected candidate probability
is greater than or equal to its configured operation threshold **and** overall
Choice confidence is greater than or equal to its configured threshold:

- `SAGE_JEV_READ_PROBABILITY_THRESHOLD` (default `0.65`)
- `SAGE_JEV_SEARCH_PROBABILITY_THRESHOLD` (default `0.75`)
- `SAGE_JEV_GRAPH_PROBABILITY_THRESHOLD` (default `0.80`)
- `SAGE_JEV_ACTION_CONFIDENCE_THRESHOLD` (default `0.50`)

All accept finite values from `0` through `1`. Candidate probability is Jev's
relative weight for that action among the offered candidates (including the
handback), while confidence describes how decisive the complete distribution is;
neither is a measured correctness rate. Lower values allow more internal work
but risk irrelevant evidence, context and operation latency. Higher values hand
back more often but do not remove the already-incurred Jev request latency.
Defaults are experimental policy values, not calibrated correctness guarantees.
On rejection, the INFO summary and `navigation.json` record both observed and
required values; the artifact also records all configured thresholds.

For exact **local** replay, set `SAGE_JEV_CAPTURE=true` before a pilot. Captures
contain sensitive prompts/source; do not upload `navigation.json`. Ordinary
navigation artifacts keep digests/locators, not raw objectives or source bodies.
Replay never contacts a model:

```bash
uv run --project apps/agent python apps/agent/evals/navigation.py replay \
  /absolute/run/navigation.json --labels /absolute/labels.json
```

Labels are separately adjudicated JSON mapping `"sequence:step"` to relevant
candidate IDs, e.g. `{"1:1": ["c0", "c2"]}`. They never enter runtime selection.
Replay compares Jev with stable-first selection under equal shortlist/count
caps. Inspect missing candidates separately from wrong selections. Captured
second steps belong to the actual trajectory, not a simulated counterfactual.
Shadow executes nothing and cannot measure a hypothetical second step.

For paired live experiments, the evaluation runner injects controls into the
same production controller. Every arm still makes paid Solver/Reviewer calls
and requires the usual Docker/model prerequisites. The normal suite never
runs this command:

```bash
uv run --env-file .env --project apps/agent python apps/agent/evals/navigation.py run \
  --arm actions-2 --repo /absolute/repository --issue-file /absolute/issue.md \
  --base-ref FIXED_BASE_SHA --allow-paid-solve
```

Arms: `off`, `deterministic-excerpts`, `jev-excerpts`, `actions-0`, `actions-1`,
`actions-2`. The zero-action control keeps objective-bearing schemas/prompts
but executes no follow-ups. Optional `--memory-file` uses normal memory setup.
Keep model versions, Issue/base, budgets, memory settings and vector-cache
state fixed within pairs; repeat to measure variance.

Copy `apps/agent/evals/navigation-manifest.example.json`, fill actual run
directories and independent quality verdicts, then compare:

```bash
uv run --project apps/agent python apps/agent/evals/navigation.py compare \
  /absolute/experiment-manifest.json
```

Supply per-model `prices` with `input_per_million`, `output_per_million`, and
optionally `cached_input_per_million` in USD applicable to the experiment.
Missing prices/usage yield unknown cost, not zero. Estimates include semantic
and embedding usage. Reports cover paired tokens/cost/wall time, Solver and
Reviewer calls, sessions, operations, added characters and independently
assessed verified completion. Counts, median/p95 and bootstrap mean intervals
are reported; one pair has no interval. Small samples are not promotion
evidence. Keep tuning and held-out runs in separate manifests.

Use fixed cases covering explicit paths, ambiguous symbols, misleading hits,
long files, test discovery, cross-file work, repairs and memory on/off.
Historical next-read agreement is only a proxy; inspect redundant reads and
failed/wasted observations alongside quality. A cheaper selector alone is not
a whole-solve saving. Live paired quality/cost/latency gates remain pending;
passing offline tests does not authorize default enablement or GitHub rollout.

## Select checks by responsibility

Paths in this table are relative to `apps/agent/tests/`.

| Changed area | Focused tests |
| --- | --- |
| CLI dispatch, help, output, exit policy | `test_cli.py`, `cli/` |
| Plan gate and model tool loop | `agents/` |
| Candidate, verification/review/repair routing | `orchestration/` |
| File paths, diffs and command execution | `repository/`, `verification/` |
| Memory build and persistence | `legion_memory/test_indexing.py`, `test_store.py` |
| Parser and source binding | `legion_memory/test_parsing.py`, `test_resolution.py` |
| Graph query/analysis semantics | `legion_memory/test_queries.py`, `test_analysis.py` |
| Issue ranking and context budgets | `legion_memory/test_retrieval.py` |
| Memory tools, visibility, deduplication and edited locators | `legion_memory/test_tools.py`, `test_session.py` |
| Vector identity, reuse, publication and cleanup | `legion_memory/test_vectors.py` |
| Provider behavior/accounting | `providers/`, `test_observability.py` |
| Atomic evidence and cleanup | `artifacts/`, `workflows/` |
| Ownership/import rules | `test_architecture.py` |

For example:

```bash
LANGSMITH_TRACING=false uv run --project apps/agent pytest \
  apps/agent/tests/test_cli.py apps/agent/tests/cli
LANGSMITH_TRACING=false uv run --project apps/agent pytest \
  apps/agent/tests/legion_memory
make github-test
make actions-check
make graph
```

`make graph` prints the bounded Solver model/tool loop, not the outer workflow.
Architecture checks traverse nested packages, resolve package-member imports,
check domain dependencies and reject cycles. The CLI initializer may only
re-export the existing entrypoint. Extraction permits more focused files while
retaining the nonblank source budget and reducing maximum internal fan-out.

Shared graph setup lives in `legion_memory/conftest.py`; use `apply_files` for
parser/store fixtures, `fixture_repo` for committed Git state, `built_memory`
for a ready service, and `memory_session` for read enrichment. Regression tests
must not import another test module for fixture construction.

Reference certification remains optional and offline:

```bash
uv run --project apps/agent pytest \
  apps/agent/tests/legion_memory/test_reference_differential.py \
  --legion-reference /absolute/path/to/trusted/reference-checkout
```

The checkout must match the fingerprints in
[`reference_manifest.json`](../apps/agent/tests/legion_memory/reference_manifest.json).
This checks normalized structural fixtures, not all language/framework behavior.

## Inspect a repository's memory without a model

Use an actual Git root with a committed `HEAD`. A nested non-repository is
rejected instead of indexing its ancestor. Pass a disposable SQLite path when
testing; do not point probes at a database that another process owns.

```bash
make legion-memory REPO=/absolute/repo \
  MEMORY_FILE=/absolute/memory/graph.sqlite3 EMBEDDINGS=off

uv run --project apps/agent sage memory status \
  --repo /absolute/repo --memory-file /absolute/memory/graph.sqlite3

make legion-retrieve REPO=/absolute/repo ISSUE=/absolute/issue.md \
  MEMORY=/absolute/memory/graph.sqlite3 EMBEDDINGS=off
```

`MEMORY_FILE` selects the build destination; `MEMORY` selects the database for
retrieval/solve. Without a build destination, the repository checkout defaults
under `.sage/legion-memory/<repo-name>-<identity-prefix>/graph.sqlite3`.
Make arguments use `REPO=...`, not a positional repository path.

The direct build equivalent is `sage memory build --repo ... --memory-file ...`.
It also accepts `--full-rebuild`. The same operation normally chooses the build
mode automatically; no separate cold/warm command is needed.

| Probe | Expected result |
| --- | --- |
| First build | `full`, ready graph, exact HEAD SHA, file/node/edge/flow/community counts |
| Repeat without commits | `no_change`, zero parsed files |
| Dirty worktree without a commit | Indexed content and SHA remain unchanged |
| Commit edits/additions/renames/deletions | Incremental reconciliation; removed identities do not linger |
| Issue names a real symbol/path | Explainable ranked locators within result/character budgets |
| Unrelated Issue | Explicit `no_match`; do not interpret as a graph failure |
| Missing, stale, foreign, corrupt or incompatible graph | Explicit error/unavailable state; no false readiness |
| Native invalid path or query pattern | Bounded rejection; no arbitrary SQL or filesystem access |

The corresponding automated tests exercise these transitions in isolated
temporary repositories. They are preferable to copying a large manual fixture.

Retrieval prints status, modes, reasons, timings and truncation. It atomically
writes `graph.context.md` and `graph.retrieval.json` beside the database for
available results, including `no_match`; unavailable retrieval does not write
a new context artifact. A previous artifact can therefore remain on disk:
use the current command result and indexed SHA when diagnosing failure.

In a solve, only `used` binds the five-tool solve profile and initial packet.
`no_match` binds no graph tools and performs no enrichment. The full registry
retains 21 read-only operations for explicit use. Read/search enrichment must
preserve source, respect ranges/caps, suppress edited locators and deduplicate
visible responses; its failure must leave source output usable.

## Exercise optional embeddings

Create configuration with `make env` and edit the existing `.env`. Set
`GEMINI_API_KEY` and review `SAGE_LEGION_*` settings in `.env.example`.
Embedding-enabled commands send bounded source-derived text and Issue queries
to Google; `SAGE_GOOGLE_MODEL_CONTEXT_APPROVED=false` prohibits that use.

```bash
make legion-memory REPO=/absolute/repo \
  MEMORY_FILE=/absolute/memory/graph.sqlite3 EMBEDDINGS=on
make legion-retrieve REPO=/absolute/repo ISSUE=/absolute/issue.md \
  MEMORY=/absolute/memory/graph.sqlite3 EMBEDDINGS=on
```

The Make targets load `ENV_FILE`; direct CLI callers export settings or use
`uv run --env-file .env`. `EMBEDDINGS=on|off` / `--embeddings on|off` overrides
the environment setting. Local memory defaults to lexical-only.

Qdrant defaults to a persistent `qdrant/` directory beside SQLite. Set a path
or server URL, never both. Local storage permits one owner; remote credentials
require HTTPS except for the supported localhost case.

Repeat the same build and expect zero new document embeddings with eligible
vectors reused. Rebuilding SQLite against the same repository/embedding identity
can reuse remote content points. Queries must remain restricted to the accepted
generation. Graph readiness and vector readiness are separate: a vector failure
preserves lexical retrieval, while standalone embedding-enabled builds exit
nonzero if vectors are unavailable.

Schema upgrades run on build. Copying SQLite does not copy Qdrant vectors.
Cleanup runs after successful publication and retains the current generation,
snapshots for at least 24 hours, and content cache for at least 30 days.
Cleanup failure reports pending without invalidating readiness. A later build
resumes acknowledged work and retries cleanup. Test these using `test_vectors.py`
before spending API resources.

The defaults remain Gemini Embedding 2, 3072 dimensions, 2,000 symbols,
300-second build deadline and concurrency 1 (configurable 1–8). Over-capacity
builds make no document calls. Raise explicit limits only after examining usage.
Missing provider token usage is `unknown`, not zero or a character estimate.

### Clear local Sage data

Clear one local data store at a time with:

```bash
make clean-runs
make clean-legion-memory
make clean-embeddings
```

Each command deletes all nested and hidden content from its matching directory
under `.sage/`. The `.sage/runs`, `.sage/legion-memory`, and `.sage/embeddings`
parent directories themselves are preserved (and created if absent). The other
stores are not changed.

## Sandbox and local solve

```bash
make env
# Set OPENAI_API_KEY and GEMINI_API_KEY in .env.
make sandbox-build
make sandbox-smoke
make doctor
make first-run REPO=/absolute/repo ISSUE=/absolute/issue.md BASE_REF=HEAD
```

The sandbox smoke uses the canonical `sage-sandbox:v2` image with networking
disabled. The image includes Git, Python 3.14, pytest, Node.js 24, npm, and
Node's built-in test runner. After pulling a change to `docker/sandbox/Dockerfile`,
rebuild the local image with `make sandbox-build`; an existing
`sage-sandbox:v2` image is not updated automatically. `make sandbox-smoke`
checks that every baseline tool is executable inside a network-disabled
container.

Solver branch navigation is covered by the focused repository and agent tests:

```bash
uv run --project apps/agent pytest -c apps/agent/pyproject.toml \
  apps/agent/tests/repository/test_git.py \
  apps/agent/tests/agents/test_solver.py
```

The tests verify branch listing, clean-worktree switching, dirty-worktree
rejection, and exposure of the structured branch tools to the Solver. The
accepted-base guard remains authoritative after any branch inspection.

Repository-specific Python and Node packages are intentionally not installed at
solve time because the sandbox has no network access. Projects needing packages
beyond the baseline must use a prepared image via `SAGE_SANDBOX_IMAGE` (or
`SANDBOX_IMAGE` for Make targets) with those locked dependencies already
installed. `first-run` validates inputs, runs offline checks, performs a live
solve and inspects its evidence. For subsequent solves:

```bash
make solve REPO=/absolute/repo ISSUE=/absolute/issue.md
make legion-solve REPO=/absolute/repo ISSUE=/absolute/issue.md \
  MEMORY=/absolute/memory/graph.sqlite3 EMBEDDINGS=off
make run-status RUN_DIR=/absolute/run-directory
make run-test RUN_DIR=/absolute/run-directory \
  TEST_COMMAND="python3 -m unittest discover -v"
```

The source checkout is not mutated. Both solve modes use an isolated checkout,
sandbox, plan gate, deterministic checks and independent review. Memory preparation
runs after sandbox startup and optional verification-tooling preflight, before
model calls. Preflight checks installed tooling without importing repository code,
running tests, installing dependencies or enabling networking.

CLI exit 0 requires a completed nonempty candidate. Other valid solve outcomes
return 2; Make reports 2 as a warning unless `REQUIRE_COMPLETED=true`. Failures
return 1. Inspect the terminal outcome and evidence, not the Make exit alone.

## GitHub publication and installation

Exercise production publication against temporary local Git substitutes:

```bash
make github-smoke
make github-smoke REPO=/absolute/repo PATCH=/absolute/diff.patch BASE_REF=HEAD
make actions-check
make github-test
make github-doctor
make github-event-check EVENT=apps/agent/tests/fixtures/github/issue_solve.json
```

The smoke must report zero model/network calls, an unchanged default branch,
a creation-only Sage branch and a draft PR request. `github-doctor` checks local
files, immutable Action references, configuration documentation and Docker
availability without reading secret values.

Accepted `/sage solve` Issue comments recheck authorization and
duplicate state before model construction, and solve at the gate's exact SHA.
GitHub memory uses fresh runner-owned SQLite and defaults to persistent remote
embeddings.

Configure credentials only as repository or environment Secrets:

```text
OPENAI_API_KEY
GEMINI_API_KEY
SAGE_LEGION_QDRANT_URL          # HTTPS endpoint
SAGE_LEGION_QDRANT_API_KEY
LANGSMITH_API_KEY               # optional; required when tracing is enabled
```

The installed Action supplies its scoped GitHub token. Secret values belong only
to the trusted solve controller step; the workflow references them with
`${{ secrets.NAME }}` and never stores their values in YAML.

Configure every GitHub-supported non-secret `.env.example` setting in the
top-level `env:` block of `.github/workflows/sage.yml`. Values are quoted strings
so booleans, numbers, empty values and JSON reach the existing typed environment
boundary unchanged. For example:

```yaml
env:
  SOLVER_MODEL: "gpt-5.4-mini"
  REVIEWER_MODEL: "gemini-3.5-flash"
  SAGE_LEGION_EMBEDDINGS_ENABLED: "true"  # false selects lexical memory
  SAGE_LEGION_EMBEDDING_MAX_NODES: "2000"
```

The action inherits this repository-owned configuration. It exposes inputs only
for credentials and run identity, preventing hidden input defaults from
overriding the YAML. `SAGE_LEGION_QDRANT_PATH` is local-only and is intentionally
omitted from the GitHub workflow, which requires remote Qdrant storage. Invalid
enabled embedding configuration fails before the first model/embedding call.

A live release canary requires a pushed implementation and both Sage Actions
pinned to its full immutable commit SHA. In a disposable repository, invoke a
bounded Issue naming a known symbol and verify:

1. Authorization, accepted SHA and one status-comment lifecycle.
2. Memory build `full` at that SHA, vectors `ready`, nonzero Qdrant operations,
   semantic/hybrid retrieval and recorded exposure/use.
3. A creation-only `sage/issue-<number>` branch and draft PR.
4. Allowlisted diagnostics without checkout, Issue body, rendered memory context,
   Qdrant endpoint or credentials.
5. Idempotent finalization; a second same-SHA Issue reuses eligible vectors.

A lexical-only canary sets `SAGE_LEGION_EMBEDDINGS_ENABLED: "false"` in the
workflow YAML and must retain the graph with vectors disabled. Restore the
intended setting afterward. Live canaries and paid calls are separate from the
offline gate.

## Read evidence before retrying

Use the [artifact map](architecture.md#evidence-and-failure-diagnosis) to inspect
the accepted input, plan, Git candidate, verification, review and terminal state.
`changed-files.json` and `diff.patch` must match the candidate workspace.
GitHub uploads intentionally contain less data than local run directories.

| Symptom | Next check |
| --- | --- |
| Dependency setup fails | Network/cache for locked packages; do not upgrade dependencies to fix a refactor |
| Docker unavailable/image missing | `docker info`, `make sandbox-build`, then `make doctor` |
| Reviewer configuration rejected | Gemini key and approved Google context use |
| Candidate rejected after review | Base SHA and digest in candidate snapshot versus final diff |
| Memory missing/stale/foreign | Same explicit database and repository; rebuild at accepted SHA |
| Corrupt/unsupported graph | Move the disposable database aside, then build a fresh graph |
| Solve memory unavailable | Artifact failure category, standalone build/status/retrieve |
| GitHub configuration failure | Remote Qdrant secrets, HTTPS URL, no local path setting |
| Vector fallback | Redacted reason and usage; transient failure may preserve lexical solving |
| GitHub workflow failure | Action tests, allowlisted diagnostics, bot-owned status comment |

Do not claim a skipped live check passed. Do not infer readiness from old context
files, model summaries or a zero-cost run that produced no verified solution.
