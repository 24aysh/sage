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
SQLite migration preservation, lexical retrieval, context lifetime and scripted Jev behavior.
There is no separately configured formatter, linter or type-checking gate.

Direct equivalent:

```bash
LANGSMITH_TRACING=false uv run --project apps/agent \
  pytest -c apps/agent/pyproject.toml apps/agent/tests
uv run --project apps/agent python -m compileall -q apps/agent/src
```

The optional pinned-reference test skips when no reference checkout is supplied.
A skip is not a parity certification.

## Jev relevance filter

The pipeline is lexical/graph retrieval → one batched Jev file judgment → bounded
Solver context. It no longer performs optional tool calls inside read/search
responses. `make legion-memory` remains model-free; `make legion-retrieve` uses
the same filter as a memory-enabled solve.

First remove retired `SAGE_JEV_NAVIGATION_POLICY`, `SAGE_JEV_MAX_FOLLOWUP_ACTIONS`,
`SAGE_JEV_RUN_WAIT_SECONDS`, and the old READ/SEARCH/GRAPH probability and ACTION
confidence threshold variables from `.env`. Configure:

```dotenv
TYPESAFE_API_KEY=your-key
SAGE_JEV_NAVIGATION_MODE=on
SAGE_JEV_MODEL=jev-1.13.0
SAGE_JEV_RELEVANCE_SCORE_THRESHOLD=2.0
SAGE_JEV_RELEVANCE_CONFIDENCE_THRESHOLD=0.5
SAGE_JEV_TIMEOUT_SECONDS=2
SAGE_JEV_LOG_INPUT=false
SAGE_JEV_CAPTURE=false
```

Mode `off` needs no Jev key and preserves lexical results. `shadow` pays for a
judgment but does not remove anything. `on` applies both thresholds, inclusive.
The score measures relevance on a described 0–3 scale; confidence measures the
concentration of the returned distribution. These provisional thresholds need
calibration on actual Issues, not interpretation as solve-success probabilities.

```bash
make legion-memory REPO=/absolute/repo MEMORY_FILE=/absolute/memory/graph.sqlite3
make legion-retrieve REPO=/absolute/repo ISSUE=/absolute/issue.md \
  MEMORY=/absolute/memory/graph.sqlite3
make legion-solve REPO=/absolute/repo ISSUE=/absolute/issue.md \
  MEMORY=/absolute/memory/graph.sqlite3
# Equivalent memory-enabled solve:
make solve REPO=/absolute/repo ISSUE=/absolute/issue.md \
  MEMORY=/absolute/memory/graph.sqlite3
make solve-baseline REPO=/absolute/repo ISSUE=/absolute/issue.md
```

Make loads the selected `ENV_FILE` (default `.env`), so edit that file to select
mode; a shell variable can be overwritten by values sourced from the env file.
`solve-baseline` forcibly disables Jev and omits memory. `make solve` without
`MEMORY` has no retrieval to filter and therefore makes no Jev request.

`legion-retrieve` prints candidate-file count, relevant files after filtering,
discarded retrieval-item and file counts, unjudged/withheld counts, context-budget
omissions, Jev time, and input/output tokens. Multiple retrieved symbols in one
file share a decision, so item counts and file counts can differ. Off-mode output
explicitly says that lexical results are not Jev-approved; shadow reports what
it would discard separately. Paths and terminal controls are safely rendered.

Inspect `graph.relevance.json`, `graph.retrieval.json`, and `graph.context.md`
beside the database. Only accepted items reach the context file in `on` mode.
No survivors is a valid `no_match` result and exit 0. Failure to judge candidates
withholds them, reports `unavailable`, and exits 1; normal solve continues with
source tools. A stale/unavailable graph still fails before filtering. Check the
current status rather than treating an old context file as current output.

There is at most one batched request before the first Solver call, no retries,
and no additional Jev requests for repairs. The complete Issue must fit 6,000
characters and the encoded request 16,000 bytes; oversize is reported as withheld,
not as irrelevance. Final context budgets still apply after selection. Explicit
source/graph tools remain usable; automatic structural enrichment is disabled
with mode `on` to prevent immediate reintroduction of rejected candidates.

Solve artifacts include `relevance-filter.json` and semantic calls in `usage.json`.
The `solver-context` timing stage is part of Solver time including Jev, and the
Jev duration remains separately accounted. Existing summaries report elapsed
time per role, Solver including/excluding Jev, Jev, Reviewer, and total solve time.

`SAGE_JEV_LOG_INPUT=true` logs the full bounded request in mode `on`. This can
include private Issue text and repository metadata: review before sharing. The
API credential is redacted, but arbitrary secrets inside Issue text are not.
`SAGE_JEV_CAPTURE=true` saves request/response data locally for replay. GitHub
keeps both off and sanitizes exported diagnostics.

```bash
uv run --project apps/agent pytest apps/agent/tests/harness/jev \
  apps/agent/tests/harness/memory/test_relevance_pipeline.py
uv run --project apps/agent python apps/agent/evals/navigation.py replay \
  /absolute/run/relevance-filter.json
uv run --env-file .env --project apps/agent python apps/agent/evals/navigation.py run \
  --arm on --repo /absolute/repo --issue-file /absolute/issue.md \
  --base-ref FIXED_SHA --memory-file /absolute/memory/graph.sqlite3 --allow-paid-solve
uv run --project apps/agent python apps/agent/evals/navigation.py compare \
  /absolute/manifest.json
```

Run arms are `off`, `shadow`, and `on`. Use the example manifest, matching Issue,
base, model settings, cache state and independent quality checks. Unknown token
usage stays unknown; old navigation/embedding runs require their historical
evaluator. Offline tests use fakes and never establish live quality or savings.

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
input tokens are a subset of input tokens and are not added twice. Graph memory
has no separate model usage.

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

## Select checks by responsibility

Paths in this table are relative to `apps/agent/tests/`.

| Changed area | Focused tests |
| --- | --- |
| CLI dispatch, help, output, exit policy | `test_cli.py`, `cli/` |
| Plan gate and model tool loop | `agents/` |
| Role instructions, packet limits and context lifetime | `harness/context/` |
| Jev decisions, transport, budgets and evaluation | `harness/jev/` |
| Candidate, verification/review/repair routing | `orchestration/` |
| File paths, diffs and command execution | `repository/`, `verification/` |
| Memory build and persistence | `harness/memory/test_indexing.py`, `test_store.py` |
| Parser and source binding | `harness/memory/test_parsing.py`, `test_resolution.py` |
| Graph query/analysis semantics | `harness/memory/test_queries.py`, `test_analysis.py` |
| Issue ranking and context budgets | `harness/memory/test_retrieval.py` |
| Memory tools, visibility, deduplication and edited locators | `harness/memory/test_tools.py`, `test_session.py` |
| Provider behavior/accounting | `providers/`, `test_observability.py` |
| Atomic evidence and cleanup | `artifacts/`, `workflows/` |
| Ownership/import rules | `test_architecture.py` |

For example:

```bash
LANGSMITH_TRACING=false uv run --project apps/agent pytest \
  apps/agent/tests/test_cli.py apps/agent/tests/cli
LANGSMITH_TRACING=false uv run --project apps/agent pytest \
  apps/agent/tests/harness/memory
make github-test
make actions-check
make graph
```

`make graph` prints the bounded Solver model/tool loop, not the outer workflow.
Architecture checks traverse nested packages, resolve package-member imports,
check domain dependencies and reject cycles. The CLI initializer may only
re-export the existing entrypoint. Extraction permits more focused files while
retaining the nonblank source budget and bounding internal imports. The workflow
coordinates the context and memory preparation owners explicitly.

Shared graph setup lives in `harness/memory/conftest.py`; use `apply_files` for
parser/store fixtures, `fixture_repo` for committed Git state, `built_memory`
for a ready service, and `memory_session` for read enrichment. Regression tests
must not import another test module for fixture construction.

Reference certification remains optional and offline:

```bash
uv run --project apps/agent pytest \
  apps/agent/tests/harness/memory/test_reference_differential.py \
  --legion-reference /absolute/path/to/trusted/reference-checkout
```

The checkout must match the fingerprints in
[`reference_manifest.json`](../apps/agent/tests/harness/memory/reference_manifest.json).
This checks normalized structural fixtures, not all language/framework behavior.

## Inspect a repository's lexical memory

Use an actual Git root with a committed `HEAD`. A nested non-repository is
rejected instead of indexing its ancestor. Pass a disposable SQLite path when
testing; do not point probes at a database that another process owns.

```bash
make legion-memory REPO=/absolute/repo \
  MEMORY_FILE=/absolute/memory/graph.sqlite3

uv run --project apps/agent sage memory status \
  --repo /absolute/repo --memory-file /absolute/memory/graph.sqlite3

make legion-retrieve REPO=/absolute/repo ISSUE=/absolute/issue.md \
  MEMORY=/absolute/memory/graph.sqlite3
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

Go (`.go`), Rust (`.rs`), C++ (`.cpp`, `.cc`, `.cxx`, `.hpp`, `.hh`, `.hxx`,
`.ipp`, `.tpp`), HTML (`.html`, `.htm`), and CSS (`.css`) support lexical symbol
and path retrieval. `.h` retains the existing C grammar. Try an Issue naming a
real function, struct, element ID, or selector; namespaced C++ implementations
use the function name, not a parameter name. Parser version 6 automatically
rebuilds older indexes. This is structural retrieval, not compiler-level name
resolution or macro expansion. Focused checks:

```bash
uv run --project apps/agent pytest apps/agent/tests/harness/memory/test_parsing.py \
  apps/agent/tests/harness/memory/test_retrieval.py
```

For an HTML/CSS repository, commit an `.html` page and its `.css` files before
building, then confirm `Languages` includes `html` and `css`. A selector named
in an Issue (for example, `.checkout-button`) should appear in retrieval, while
local `<link rel="stylesheet">`, `<script src>`, and CSS `@import` paths should
be visible through `imports_of`/`importers_of`. Inline `<style>` content,
anonymous tags, remote URLs, and runtime-generated class names are intentionally
not inferred.

Retrieval prints status, modes, reasons, timings and truncation. It atomically
writes `graph.context.md` and `graph.retrieval.json` beside the database for
available results, including `no_match`; unavailable retrieval does not write
a new context artifact. A previous artifact can therefore remain on disk:
use the current command result and indexed SHA when diagnosing failure.

In a solve, only `used` binds the five-tool solve profile and initial packet.
`no_match` binds no graph tools and performs no enrichment. The full registry
retains 21 read-only operations for explicit use. With Jev off/shadow, read/search enrichment must
preserve source, respect ranges/caps, suppress edited locators and deduplicate
visible responses; its failure must leave source output usable.

## Graph-only memory and migration

Legion Memory always uses local lexical search and graph relationships. Building
and retrieving memory require no model keys, Qdrant, or network access:

```bash
make legion-memory REPO=/absolute/repo MEMORY_FILE=/absolute/memory/graph.sqlite3
make legion-retrieve REPO=/absolute/repo ISSUE=/absolute/issue.md \
  MEMORY=/absolute/memory/graph.sqlite3
```

Run a build once on older databases to migrate to schema 4. Expect graph rows
and provenance to survive while obsolete vector metadata is removed. Repeat the
build at the same SHA and expect `no_change`. No external vector store is opened.
Old Qdrant files and server collections remain user-owned; remove them manually
only when no other consumer needs them. Old `SAGE_LEGION_EMBEDDING_*`,
`SAGE_LEGION_EMBEDDINGS_ENABLED`, and `SAGE_LEGION_QDRANT_*` values have no effect
and can be removed from local `.env` and repository Secrets. Remove `EMBEDDINGS=`
from Make invocations and `--embeddings` from direct CLI commands.

### Clear local Sage data

Clear one local data store at a time with:

```bash
make clean-runs
make clean-legion-memory
```

Each command deletes all nested and hidden content from its matching directory
under `.sage/`. The `.sage/runs` and `.sage/legion-memory`
parent directories themselves are preserved (and created if absent). The other
stores are not changed.

## Sandbox and local solve

### Persistent role instructions

Commit `sage-solver.md` and `sage-reviewer.md` in the target repository. Sage
discovers them automatically at the selected base SHA and includes each only
in its role's system message on every call, including repairs and rereviews.
The contents are loaded once before sandbox startup; editing them during a solve
does not change the active policy. Missing files are allowed. Keep them concise:
they consume input tokens each call, and each file is capped at 12,000 UTF-8 bytes.

Configure alternate repository-relative paths in `.env` or `sage.yml`:

```yaml
env:
  SAGE_SOLVER_INSTRUCTIONS_FILE: "sage-solver.md"
  SAGE_REVIEWER_INSTRUCTIONS_FILE: "sage-reviewer.md"
```

No enable switch is needed. `make solve`, `make solve-baseline`, `make legion-solve`
and GitHub use the same role context path. The baseline still disables graph
memory and Jev. To verify instruction lifetime and isolation without paid calls:

```bash
uv run --project apps/agent pytest apps/agent/tests/harness/context \
  apps/agent/tests/orchestration/test_solve_orchestrator.py \
  apps/agent/tests/workflows/test_solve_workflow.py
```

### Run a local solve

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
  MEMORY=/absolute/memory/graph.sqlite3
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
GitHub memory uses fresh runner-owned SQLite with lexical retrieval and no
external memory service.

Configure credentials only as repository or environment Secrets:

```text
OPENAI_API_KEY
GEMINI_API_KEY
TYPESAFE_API_KEY               # optional; required when GitHub Jev mode is shadow/on
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
  SAGE_SOLVER_INSTRUCTIONS_FILE: "sage-solver.md"
  SAGE_REVIEWER_INSTRUCTIONS_FILE: "sage-reviewer.md"
  SAGE_JEV_NAVIGATION_MODE: "off"         # change only for an explicit canary
  SAGE_JEV_RELEVANCE_SCORE_THRESHOLD: "2.0"
  SAGE_JEV_RELEVANCE_CONFIDENCE_THRESHOLD: "0.5"
  SAGE_JEV_LOG_INPUT: "false"
  SAGE_JEV_CAPTURE: "false"
```

The action inherits this repository-owned configuration. It exposes inputs only
for credentials and run identity, preventing hidden input defaults from
overriding the YAML. Memory requires no credentials. Invalid Jev configuration
fails before the first model call. The TypeSafe secret is optional while mode is
`off`; `shadow` and `on` require it. Keep `SAGE_JEV_LOG_INPUT=false` and
`SAGE_JEV_CAPTURE=false` on GitHub: raw Issue/source bodies belong only in
deliberate local evaluation evidence.

A live release canary requires a pushed implementation and both Sage Actions
pinned to its full immutable commit SHA. In a disposable repository, invoke a
bounded Issue naming a known symbol and verify:

1. Authorization, accepted SHA and one status-comment lifecycle.
2. Memory build `full` at that SHA, exact/FTS retrieval and recorded exposure/use.
   No embedding settings, API calls, or Qdrant secret requirements.
3. A creation-only `sage/issue-<number>` branch and draft PR.
4. Allowlisted diagnostics without checkout, Issue body, rendered memory context,
   or credentials.
5. Idempotent finalization; each GitHub run has its own graph snapshot.

For a Jev canary, add the `TYPESAFE_API_KEY` repository/environment Secret and
first set `SAGE_JEV_NAVIGATION_MODE: "shadow"`. Verify
`usage.json` contains separately accounted semantic calls and the uploaded
`relevance-filter.json` contains operational counts and timings but no capture,
file paths, Issue text or source. Shadow must leave context selection unchanged. Only an explicitly approved second
canary should use `on`; restore mode to `off` afterward. Compare it with a fixed
Issue/base/model baseline and do not treat a successful canary as promotion or
an efficiency result.

Live canaries and paid calls remain separate from the offline gate.

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
| GitHub workflow failure | Action tests, allowlisted diagnostics, bot-owned status comment |

Do not claim a skipped live check passed. Do not infer readiness from old context
files, model summaries or a zero-cost run that produced no verified solution.
