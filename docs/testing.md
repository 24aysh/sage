# Testing Sage

This guide covers offline development checks, Legion Memory Phases 1 through 3, the
Docker boundary, local live solves, and the controlled GitHub rollout check.
Run commands from the repository root.

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

## Legion Memory Phase 1 testing

Phase 1 covers the native graph engine, SQLite persistence, read-only graph
tools, and standalone build/status commands. It does not cover Issue-specific
retrieval, Solver integration, or GitHub Actions integration; those belong to
later phases.

All Phase 1 checks are offline. They require no provider credentials, Docker,
MCP server, network service, or paid embedding call. The first `make setup`
still needs access to the locked Python packages if they are not already
installed.

### Command reference

Build or update the default database:

```bash
make legion-memory REPO=/absolute/path/to/repository
```

Select a database explicitly when testing so it is easy to inspect and remove:

```bash
make legion-memory \
  REPO=/absolute/path/to/repository \
  MEMORY_FILE=/absolute/path/to/graph.sqlite3
```

GNU Make treats `make legion-memory /path/to/repo` as two targets. Always pass
the repository through `REPO=...`.

The direct CLI equivalents are:

```bash
uv run --project apps/agent sage memory build \
  --repo /absolute/path/to/repository \
  --memory-file /absolute/path/to/graph.sqlite3

uv run --project apps/agent sage memory status \
  --repo /absolute/path/to/repository \
  --memory-file /absolute/path/to/graph.sqlite3
```

Use `--full-rebuild` with the direct build command only when deliberately
testing a forced rebuild. Normal callers should let the single build operation
choose `full`, `incremental`, or `no_change`.

Without `MEMORY_FILE`, commands run from the Sage repository root use:

```text
.sage/legion-memory/<repo-name>-<repository-id-prefix>/graph.sqlite3
```

The directory is ignored by Git. The build output must include the resolved
file, build type, indexed SHA, files indexed/parsed/removed, node/edge/flow/
community totals, detected languages, warnings, duration, and a clear result.

### Automated Phase 1 gate

Run the focused suite while developing the engine:

```bash
LANGSMITH_TRACING=false uv run --project apps/agent \
  pytest -c apps/agent/pyproject.toml apps/agent/tests/legion_memory
```

Run the entrypoint and ownership tests affected by Phase 1:

```bash
LANGSMITH_TRACING=false uv run --project apps/agent \
  pytest -c apps/agent/pyproject.toml \
  apps/agent/tests/test_cli.py \
  apps/agent/tests/test_makefile.py \
  apps/agent/tests/test_composition.py \
  apps/agent/tests/test_architecture.py
```

Finish with the canonical repository gates:

```bash
make check
make graph
```

The focused tests use temporary Git repositories and SQLite files. They cover:

- every declared Tree-sitter grammar and stable repository-relative identity;
- class, function, test, import, call, inheritance, and source-range parsing;
- full, no-change, and incremental add/change/delete/rename behavior;
- incoming-edge retention when a symbol remains stable across an update;
- FTS5 search, SQL-shaped input, result limits, and valid bounded JSON;
- impact, traversal, flow, community, hub, bridge, and knowledge-gap analysis;
- schema migration, WAL mode, rollback, concurrent reading, and corrupt data;
- missing, stale, foreign, and unavailable graph behavior;
- every native LangChain tool's bound schema and read-only adapter; and
- CLI, Makefile, composition, attribution, and dependency-direction contracts.

No normal test may access a developer's real Legion Memory cache or require a
live model.

### Create a disposable acceptance repository

Run the manual checks from the Sage repository root. Keep the repository and
database under one disposable directory:

```bash
LEGION_TEST_ROOT="$(mktemp -d)"
LEGION_TEST_REPO="$LEGION_TEST_ROOT/repository"
LEGION_TEST_DB="$LEGION_TEST_ROOT/graph.sqlite3"
mkdir -p "$LEGION_TEST_REPO/tests"

git -C "$LEGION_TEST_REPO" init --initial-branch=main
git -C "$LEGION_TEST_REPO" config user.name "Legion Memory Test"
git -C "$LEGION_TEST_REPO" config user.email "legion@example.invalid"

cat >"$LEGION_TEST_REPO/service.py" <<'PY'
class Base:
    pass


class Worker(Base):
    def run(self):
        return helper()


def helper():
    return 42
PY

cat >"$LEGION_TEST_REPO/app.py" <<'PY'
from service import Worker


def main():
    return Worker().run()
PY

cat >"$LEGION_TEST_REPO/tests/test_service.py" <<'PY'
from service import helper


def test_helper():
    assert helper() == 42
PY

cat >"$LEGION_TEST_REPO/obsolete.py" <<'PY'
def obsolete():
    return None
PY

git -C "$LEGION_TEST_REPO" add --all
git -C "$LEGION_TEST_REPO" commit -m "test: add initial graph fixture"
```

All graph content comes from committed `HEAD` blobs. This is important: an
uncommitted edit must never be labeled with the accepted Git SHA.

### Check the first full build

Capture the clean source status, then build:

```bash
LEGION_STATUS_BEFORE="$(git -C "$LEGION_TEST_REPO" status --short --untracked-files=all)"

make legion-memory \
  REPO="$LEGION_TEST_REPO" \
  MEMORY_FILE="$LEGION_TEST_DB"

git -C "$LEGION_TEST_REPO" status --short --untracked-files=all
```

Expected results:

- result is `ready` and build type is `full`;
- indexed SHA equals `git -C "$LEGION_TEST_REPO" rev-parse HEAD`;
- four files are indexed and four files are parsed;
- node and edge totals are non-zero;
- the memory file is outside the source repository; and
- Git status after the build equals `LEGION_STATUS_BEFORE` (empty here).

Inspect the public status command:

```bash
uv run --project apps/agent sage memory status \
  --repo "$LEGION_TEST_REPO" \
  --memory-file "$LEGION_TEST_DB"
```

It must report `ready`, the same SHA and database path, and the same aggregate
counts as the build. The SQLite inspection below verifies schema metadata.

### Inspect SQLite invariants

Use Python's standard-library SQLite driver so the check does not depend on a
separate `sqlite3` executable:

```bash
uv run --project apps/agent python - "$LEGION_TEST_DB" <<'PY'
import sqlite3
import sys

database = sys.argv[1]
connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)

metadata = dict(connection.execute("SELECT key, value FROM metadata"))
counts = {
    table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    for table in ("files", "nodes", "edges", "flows", "communities")
}
duplicate_nodes = connection.execute(
    "SELECT count(*) - count(DISTINCT qualified_name) FROM nodes"
).fetchone()[0]
helper = connection.execute(
    "SELECT kind, file_path, line_start, line_end "
    "FROM nodes WHERE name = ?",
    ("helper",),
).fetchall()

print("build_state:", metadata["build_state"])
print("indexed_sha:", metadata["indexed_sha"])
print("schema_version:", metadata["schema_version"])
print("counts:", counts)
print("duplicate qualified nodes:", duplicate_nodes)
print("helper:", helper)
PY
```

Expect `build_state: ready`, schema version `1`, positive node and edge counts,
zero duplicate qualified nodes, and a `helper` locator in `service.py`.

The table-name interpolation above is over a fixed local allowlist. Production
tool inputs are parameterized and never accept arbitrary SQL.

### Check the no-change path

Run the identical command again:

```bash
make legion-memory \
  REPO="$LEGION_TEST_REPO" \
  MEMORY_FILE="$LEGION_TEST_DB"
```

Expect build type `no_change`, zero files parsed, zero files removed, and
unchanged file/node/edge totals. Rerun the SQLite duplicate query and continue
to expect zero.

To prove dirty worktree content is not indexed, edit `service.py` without
committing it and run the build again. It should still report `no_change` at
the same SHA. Restore this disposable fixture before continuing:

```bash
printf '\n# uncommitted probe\n' >>"$LEGION_TEST_REPO/service.py"
make legion-memory REPO="$LEGION_TEST_REPO" MEMORY_FILE="$LEGION_TEST_DB"
git -C "$LEGION_TEST_REPO" restore service.py
```

### Check incremental change, add, rename, and delete

Perform each operation in a separate commit and rerun the same build command.
This makes a failed reconciliation easy to identify.

Change one tracked source file:

```bash
sed -i 's/return 42/return 84/' "$LEGION_TEST_REPO/service.py"
git -C "$LEGION_TEST_REPO" add service.py
git -C "$LEGION_TEST_REPO" commit -m "test: change helper"
make legion-memory REPO="$LEGION_TEST_REPO" MEMORY_FILE="$LEGION_TEST_DB"
```

Expect `incremental`, one parsed file, and zero removed files.

Add a source file:

```bash
cat >"$LEGION_TEST_REPO/added.py" <<'PY'
from service import helper


def added():
    return helper()
PY
git -C "$LEGION_TEST_REPO" add added.py
git -C "$LEGION_TEST_REPO" commit -m "test: add caller"
make legion-memory REPO="$LEGION_TEST_REPO" MEMORY_FILE="$LEGION_TEST_DB"
```

Expect `incremental`, one parsed file, zero removed files, and a larger graph.

Rename a source file:

```bash
git -C "$LEGION_TEST_REPO" mv app.py entry.py
git -C "$LEGION_TEST_REPO" commit -m "test: rename entrypoint"
make legion-memory REPO="$LEGION_TEST_REPO" MEMORY_FILE="$LEGION_TEST_DB"
```

Expect `incremental`, one parsed file and one removed file. `entry.py` must be
present in `files`; `app.py` must be absent.

Delete a source file:

```bash
git -C "$LEGION_TEST_REPO" rm obsolete.py
git -C "$LEGION_TEST_REPO" commit -m "test: remove obsolete source"
make legion-memory REPO="$LEGION_TEST_REPO" MEMORY_FILE="$LEGION_TEST_DB"
```

Expect `incremental`, zero parsed files and one removed file. No node may retain
`obsolete.py` as its `file_path`.

After every build, the reported indexed SHA must equal repository `HEAD`, the
database must remain `ready`, and the repository must remain clean.

### Probe the native read-only operations

Phase 1 implements native agent adapters but does not bind them to the Solver
yet. Exercise their underlying production service directly:

```bash
uv run --project apps/agent python - \
  "$LEGION_TEST_REPO" "$LEGION_TEST_DB" <<'PY'
import json
import sys
from pathlib import Path

from sage.legion_memory.service import LegionMemoryService

repository = Path(sys.argv[1])
database = Path(sys.argv[2])
service = LegionMemoryService()
common = {"repo_root": repository, "memory_file": database}

results = {
    "search": service.semantic_search_nodes_tool(query="helper", **common),
    "callers": service.query_graph_tool(
        pattern="callers_of", target="helper", **common
    ),
    "imports": service.query_graph_tool(
        pattern="imports_of", target="added.py", **common
    ),
    "tests": service.query_graph_tool(
        pattern="tests_for", target="helper", **common
    ),
    "impact": service.get_impact_radius_tool(
        changed_files=["service.py"], **common
    ),
    "flows": service.list_flows_tool(limit=5, **common),
    "communities": service.list_communities_tool(limit=5, **common),
    "architecture": service.get_architecture_overview_tool(
        max_communities=5, **common
    ),
}

for name, result in results.items():
    print(name, json.dumps(result, sort_keys=True))
PY
```

For every result, verify:

- `status` is `ok` or `ready`;
- `repository_id` and `indexed_sha` are present;
- `returned` does not exceed `total`;
- paths are repository-relative and line locators are bounded;
- search reports its actual `search_mode`; and
- an empty relationship includes cautious confidence language rather than
  claiming the relationship cannot exist dynamically.

The `helper` query should find callers from `Worker.run`, `added`, and
`test_helper`. Use the locators to inspect source; graph evidence is not a
replacement for source truth.

### Check expected failures

Standalone Phase 1 build/status commands are strict. They return non-zero
instead of silently falling back; the Phase 2 retrieval command reports its
explicit fallback status before exiting.

Missing database:

```bash
uv run --project apps/agent sage memory status \
  --repo "$LEGION_TEST_REPO" \
  --memory-file "$LEGION_TEST_ROOT/missing.sqlite3"
echo "$?"
```

Expect status `missing` and exit code `1`.

Stale database: commit a change and check status before rebuilding:

```bash
cat >"$LEGION_TEST_REPO/stale.py" <<'PY'
def stale_probe():
    return True
PY
git -C "$LEGION_TEST_REPO" add stale.py
git -C "$LEGION_TEST_REPO" commit -m "test: advance accepted sha"

uv run --project apps/agent sage memory status \
  --repo "$LEGION_TEST_REPO" \
  --memory-file "$LEGION_TEST_DB"
echo "$?"

make legion-memory REPO="$LEGION_TEST_REPO" MEMORY_FILE="$LEGION_TEST_DB"
```

Status before the rebuild must fail with an exact-SHA error. The build then
selects `incremental` or `full` safely and restores `ready` status.

Foreign database: initialize a second repository and pass it the first
repository's database:

```bash
LEGION_FOREIGN_REPO="$LEGION_TEST_ROOT/foreign"
mkdir -p "$LEGION_FOREIGN_REPO"
git -C "$LEGION_FOREIGN_REPO" init --initial-branch=main
git -C "$LEGION_FOREIGN_REPO" config user.name "Legion Memory Test"
git -C "$LEGION_FOREIGN_REPO" config user.email "legion@example.invalid"
printf 'def foreign():\n    return True\n' >"$LEGION_FOREIGN_REPO/main.py"
git -C "$LEGION_FOREIGN_REPO" add main.py
git -C "$LEGION_FOREIGN_REPO" commit -m "test: add foreign repository"

make legion-memory REPO="$LEGION_FOREIGN_REPO" MEMORY_FILE="$LEGION_TEST_DB"
echo "$?"

uv run --project apps/agent sage memory status \
  --repo "$LEGION_TEST_REPO" \
  --memory-file "$LEGION_TEST_DB"
```

The foreign build must fail non-zero and state that the file belongs to a
different repository. The original repository's status must remain `ready`.

Corrupt database: operate on a copy, never the last good database:

```bash
cp "$LEGION_TEST_DB" "$LEGION_TEST_ROOT/corrupt.sqlite3"
printf 'not sqlite\n' >"$LEGION_TEST_ROOT/corrupt.sqlite3"
uv run --project apps/agent sage memory status \
  --repo "$LEGION_TEST_REPO" \
  --memory-file "$LEGION_TEST_ROOT/corrupt.sqlite3"
echo "$?"

uv run --project apps/agent sage memory status \
  --repo "$LEGION_TEST_REPO" \
  --memory-file "$LEGION_TEST_DB"
```

The corrupt copy must fail non-zero, while the original must remain `ready`.
Automated tests separately force a post-processing failure and verify that the
transaction preserves the previous ready SHA, state, and node count.

Unsupported or binary source files are skipped by the declared inventory and
parser rules. A supported source file with Tree-sitter syntax errors may
produce a bounded warning and declared graph gap; inspect warnings before
trusting coverage.

### Sage repository smoke test

Finally, exercise the real repository with a fresh disposable database:

```bash
LEGION_SAGE_DB="$LEGION_TEST_ROOT/sage.sqlite3"
make legion-memory REPO="$(pwd)" MEMORY_FILE="$LEGION_SAGE_DB"
make legion-memory REPO="$(pwd)" MEMORY_FILE="$LEGION_SAGE_DB"
uv run --project apps/agent sage memory status \
  --repo "$(pwd)" \
  --memory-file "$LEGION_SAGE_DB"
git status --short --untracked-files=all
```

The first run must be `full`; the second must be `no_change`. Expect non-zero
file, node, edge, flow, and community counts. No tracked source status may
change. To test default placement separately, omit `MEMORY_FILE` and confirm
that the printed path is under ignored `.sage/legion-memory/`.

### Phase 1 acceptance checklist

- [ ] Focused Legion Memory tests pass.
- [ ] CLI, Makefile, composition, and architecture tests pass.
- [ ] `make check` and `make graph` pass.
- [ ] A new database produces a successful `full` build.
- [ ] Repeating the build produces `no_change` without duplicates.
- [ ] Committed add/change/rename/delete operations reconcile incrementally.
- [ ] Status and SQLite metadata match the repository's exact `HEAD` SHA.
- [ ] Known symbols, callers, imports, tests, flows, and communities match
      direct source inspection.
- [ ] Native results are bounded, provenance-bearing, and repository-relative.
- [ ] Missing, stale, foreign, and corrupt databases fail clearly.
- [ ] A failed update preserves the last ready transaction.
- [ ] The target repository is not mutated by graph creation or queries.
- [ ] No MCP process, network service, model, provider credential, or paid
      embedding is required.

## Legion Memory Phase 2 retrieval check

Phase 2 retrieval is deterministic and offline. It validates that the selected
database belongs to the selected repository at its exact current `HEAD`, then
uses exact identifiers and paths, FTS5, and bounded graph expansion. It does
not call a model, embedding provider, MCP server, or network service.

Create an Issue file, build the graph, and retrieve memories:

```bash
cat > /tmp/legion-issue.md <<'EOF'
# Helper returns the wrong value

The `helper` function in service.py returns an incorrect result. Check its
callers and related tests.
EOF

make legion-memory \
  REPO=/absolute/path/to/repository \
  MEMORY_FILE=/tmp/legion-graph.sqlite3

make legion-retrieve \
  REPO=/absolute/path/to/repository \
  ISSUE=/tmp/legion-issue.md \
  MEMORY=/tmp/legion-graph.sqlite3
```

The retrieval log starts with one of these states:

- `Legion Memory retrieval: used` and `Memory used: yes`: useful context was
  returned. The `Retrieved memories` section prints each ranked symbol, its
  source location, score, and reasons such as `exact_identifier`, `path_match`,
  `fts`, `caller_of`, `test_for`, `same_flow`, or `same_community`.
- `Legion Memory retrieval: no_match` and `Memory used: no`: the graph is
  healthy, but there were no lexical candidates or none passed the usefulness
  threshold. This exits zero because it is a valid retrieval outcome.
- `Legion Memory retrieval: unavailable` and `Memory used: no`: the database
  is missing, stale, foreign, corrupt, locked, or schema-incompatible. This
  exits non-zero; rebuild the graph for the repository's current `HEAD`.

Every result also prints the exact indexed SHA, actual search modes, normalized
query terms, lexical and graph-expanded candidate counts, returned/omitted
counts, truncation, context characters, and duration. Graph text is treated as
untrusted data and only repository-relative source locators are returned.

Run the focused Phase 2 tests with:

```bash
uv run --project apps/agent pytest -c apps/agent/pyproject.toml \
  apps/agent/tests/legion_memory/test_retrieval.py \
  apps/agent/tests/test_cli.py \
  apps/agent/tests/test_makefile.py
```

## Legion Memory Phase 3 local solve check

Use the existing command as the no-memory baseline:

```bash
make solve \
  REPO=/absolute/path/to/repository \
  ISSUE=/absolute/path/to/issue.md \
  BASE_REF=<exact-commit>
```

Then run the same Issue, base commit, models, and budgets with explicit memory:

```bash
make legion-solve \
  REPO=/absolute/path/to/repository \
  ISSUE=/absolute/path/to/issue.md \
  MEMORY=/absolute/path/to/graph.sqlite3 \
  BASE_REF=<exact-commit>
```

`legion-solve` calls the same local solve workflow with `--memory-file`. It
builds, incrementally updates, or confirms the supplied graph after preparing
the clean exact-SHA workspace and before starting the sandbox or model. The
database does not need to exist beforehand. `make solve` does not request,
build, retrieve, or expose memory.

A memory run prints two pre-solve panels:

- `Legion Memory: graph ready` reports build type, base SHA, file and graph
  counts, and the SQLite path;
- `Legion Memory: retrieval` reports `used`, `no_match`, or `unavailable`,
  match counts, relevant paths, and whether normal repository inspection is
  the fallback.

`used` adds bounded context and the native graph tools to the Solver.
`no_match` adds no memory prompt context but keeps graph tools available for
manual exploration. `unavailable` runs the normal Solver without graph tools.
All paths still require the saved plan, current source reads, deterministic
verification, and independent review.

At the end, both commands print the same comparison fields:

```text
Usage totals:
  Model calls: ...
  Total tool calls: ...
  Tools: read_file=..., semantic_search_nodes_tool=..., ...
  Input tokens: ...
  Output tokens: ...
  Cached input tokens: ...
  Total tokens: ...
```

`Total tokens` is provider-reported input plus output tokens; cached input is
shown separately and is already part of provider input accounting when the
provider reports it that way. Tool totals count model-requested calls and do
not store their arguments. Use `usage.json` for the per-model-call ledger and,
for a memory run, `legion-memory.json` for graph build, retrieval, fallback,
and native memory-tool usage evidence.

Run the focused Phase 3 tests without models or Docker:

```bash
LANGSMITH_TRACING=false uv run --project apps/agent \
  pytest -c apps/agent/pyproject.toml \
  apps/agent/tests/workflows/test_solve_workflow.py \
  apps/agent/tests/orchestration/test_solve_orchestrator.py \
  apps/agent/tests/agents/test_solver.py \
  apps/agent/tests/providers/test_calls.py \
  apps/agent/tests/legion_memory/test_session.py \
  apps/agent/tests/test_cli.py \
  apps/agent/tests/test_makefile.py
```

For a fair local comparison, record both run directories and compare outcome,
candidate diff, verification/review evidence, `usage.json`, and the terminal
usage totals. One lower-token run is useful evidence, not proof that memory
always improves quality or cost.

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
- `usage.json` records bounded model-call and model-requested-tool provenance;
- `legion-memory.json`, when memory was requested, records safe graph,
  retrieval, fallback, and native memory-tool summaries;
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

`legion-solve` reports `unavailable`: inspect `failure_category` in
`legion-memory.json`, then retry the standalone build/status and retrieval
commands. The solve itself intentionally continues with normal repository
inspection. `no_match` is not a graph failure; refine the Issue's concrete
paths or identifiers when testing retrieval quality.

Check the installed workflow files, immutable Action pins, documentation, and
Docker availability with:

```bash
make github-doctor
```
