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
disabled. The image includes Python 3.14, pytest, Node.js 24, npm, and Node's
built-in test runner. After pulling a change to `docker/sandbox/Dockerfile`,
rebuild the local image with `make sandbox-build`; an existing
`sage-sandbox:v2` image is not updated automatically. `make sandbox-smoke`
checks that every baseline tool is executable inside a network-disabled
container.

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
