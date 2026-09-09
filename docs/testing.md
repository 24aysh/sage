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

The command atomically saves the exact bounded context and its provenance next
to the database. For the example above, the readable artifact is
`/tmp/legion-graph.context.md`. A later retrieval against the same database
replaces that file, including when the latest valid result is `no_match`, so an
older context cannot be mistaken for the current result. The command prints the
resolved path as `Context file`.

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
counts, truncation, context characters, context-file path, and duration. Graph
text is treated as untrusted data and only repository-relative source locators
are returned.

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

Solver tool-argument validation is recoverable. In particular, `write_file`
defaults an omitted `mode` to `create_or_replace`; other missing or invalid
arguments are returned to the Solver as bounded correction feedback instead of
terminating the run. Repository failures remain recoverable feedback, while
unexpected implementation errors are still surfaced.

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
  Commands: ["pytest ...", "git diff --check HEAD --"]
  Input tokens: ...
  Output tokens: ...
  Cached input tokens: ...
  Total tokens: ...
```

`Commands` lists the policy-approved Solver `run_command` invocations that
reached the repository execution boundary, in execution order. Other tool
arguments remain excluded. `Total tokens` is provider-reported input plus
output tokens; cached input is shown separately and is already part of provider
input accounting when the provider reports it that way. Tool totals count
model-requested calls and do not store their arguments except for this
dedicated command list. Use `usage.json` for the per-model-call and
executed-command ledger and,
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

The proposed [Legion Memory evaluation plan](../specs/26_LEGION_MEMORY_RETRIEVAL_EFFICIENCY_GAP_PLAN.md)
defines quality-first benchmarking with independently tested fixes, held-out
issues, paired trials, and separate retrieval diagnostics. Its `legion-eval-*`
commands are planned, not implemented. Tool counts and token totals remain
useful cost diagnostics, not correctness scores.

## Legion Memory Phase 4: embeddings and compact context

This is the initial local Phase 4 implementation, not a claim of complete
reference-behavior parity or measured token savings. It adds richer Python
metadata/resolution, JS/TS arrow-function extraction, improved flows, weighted
Leiden communities, relationship-rich context, and opt-in hybrid retrieval.

Set `GEMINI_API_KEY` in your existing `.env`. Review the new
`SAGE_LEGION_*` settings in `.env.example`; do not overwrite your real `.env`.
Embedding-enabled commands send bounded source-derived symbol text and Issue
queries to Google. `SAGE_GOOGLE_MODEL_CONTEXT_APPROVED=false` prohibits this.
The model is `gemini-embedding-2`, with 3072 dimensions by default. No OpenAI
key, chat-model call, or Docker sandbox is required for build/retrieval.

Default Qdrant storage is a persistent `qdrant/` directory beside your SQLite
file. One process may own that local store at a time. Leave both Qdrant path
and URL settings blank to use this default; configure `SAGE_LEGION_QDRANT_PATH`
or `SAGE_LEGION_QDRANT_URL`, never both, to override it. A remote server with
credentials must use HTTPS. Server deployments use the same adapter but need
their own integration/concurrency verification before shared production use.

```bash
# Build graph + vectors (the same command handles existing and new databases).
make legion-memory REPO=/absolute/repo MEMORY_FILE=/absolute/memory/graph.sqlite3 EMBEDDINGS=on

# Repeat unchanged: expect 0 embedded documents and existing vectors reused.
make legion-memory REPO=/absolute/repo MEMORY_FILE=/absolute/memory/graph.sqlite3 EMBEDDINGS=on

# Print whether memory matched, what matched, and vector/fallback status.
make legion-retrieve REPO=/absolute/repo ISSUE=/absolute/issue.md MEMORY=/absolute/memory/graph.sqlite3 EMBEDDINGS=on

# Run the same retrieval without embeddings for comparison.
make legion-retrieve REPO=/absolute/repo ISSUE=/absolute/issue.md MEMORY=/absolute/memory/graph.sqlite3 EMBEDDINGS=off
```

`EMBEDDINGS=on|off` overrides `SAGE_LEGION_EMBEDDINGS_ENABLED` from the
environment. With neither override nor opt-in setting, commands remain
lexical-only. The three Make targets load the existing configured `ENV_FILE`.
Direct CLI equivalents accept `--embeddings on|off`; direct CLI callers must
export their settings or use `uv run --env-file .env` themselves.

Inspect `<database-stem>.context.md` beside SQLite for the bounded retrieved
context, including signatures and relationship links. Retrieval reports
lexical and semantic candidate counts, contributing modes and omitted items.
Vector readiness is separate from `Memory used`: a ready index may have no
useful match, and unavailable vectors may still produce useful lexical memory.
The cosine floor defaults to 0.45 and is provisional; use labeled positive
and unrelated Issues to calibrate `SAGE_LEGION_EMBEDDING_MIN_SIMILARITY`.

An embedding-enabled build exits non-zero if vectors cannot become ready,
even if its graph build succeeded. A later build resumes acknowledged batches.
If an initial build reaches the default 300-second or 2,000-symbol budget,
inspect the counts and raise the explicit budget only if the API cost is
acceptable. Requests are sequential and individually bounded; unchanged text
is not re-embedded. Native retrieval never builds document vectors implicitly.

Existing schema-1/2 SQLite graphs migrate to schema 3 on build. The parser-version
change also rebuilds metadata/relations from committed source. Direct retrieval
of an old schema requires running the build first. Qdrant data is separate:
copying only SQLite does not copy vectors. Deleted/renamed symbols and old
generations are excluded from queries. After successful publication, cleanup
may delete snapshot points older than 24 hours and content-cache points older
than 30 days; it never removes the current generation. `Vector cleanup: complete
/ N obsolete points removed` reports this separately. This retention window
prevents one concurrent solve from deleting another solve's active generation
while preserving reuse for unchanged content. A cleanup failure reports
`pending` but keeps the new generation ready; the next build retries. Failed
indexing never deletes recovery data. Other repositories and separately
configured model/dimension collections are left alone, not treated as garbage
for the current index.

For an A/B/C comparison, replace `SAME_COMMIT` with one fixed SHA and keep
models, Issue, verification and solve budgets identical:

```bash
make solve REPO=/absolute/repo ISSUE=/absolute/issue.md BASE_REF=SAME_COMMIT
make legion-solve REPO=/absolute/repo ISSUE=/absolute/issue.md MEMORY=/absolute/memory/graph.sqlite3 BASE_REF=SAME_COMMIT EMBEDDINGS=off
make legion-solve REPO=/absolute/repo ISSUE=/absolute/issue.md MEMORY=/absolute/memory/graph.sqlite3 BASE_REF=SAME_COMMIT EMBEDDINGS=on
```

`make solve` remains memory-free even if embeddings are enabled in `.env`.
Preserve all three run directories. The existing total tool calls, executed
command list and model token totals are unchanged. Memory summaries additionally
report embedding document/query API calls (including retries), retries, input
tokens when reported, and Qdrant operations. Embedding token usage currently
shows `unknown` when Google does not supply it; it is not zero or a characters-
to-tokens estimate. Inspect `legion-memory.json` for cumulative embedding usage
and build/retrieval evidence, and `usage.json` for actual agent/model usage.

Only compare efficiency for successful verified/reviewed candidates. Separate
cold indexing from warm retrieval and repeat comparisons; neither matching
nodes nor one cheaper run proves a general improvement. No live efficiency
claim is made by the offline tests below.

```bash
# Fake Gemini + real temporary local Qdrant; no API keys or network required.
uv run --project apps/agent pytest apps/agent/tests/legion_memory
make check
make graph
make github-smoke
make github-doctor
```

## Legion retrieval-efficiency changes (spec 26, A–E)

Run offline coverage, including fake embeddings with 2,001 eligible symbols,
interrupted/resumed builds, semantic distractors, static composition, JSONC,
repair histories and environment failures:

```bash
make check
uv run --project apps/agent pytest apps/agent/tests/legion_memory/test_reference_differential.py --legion-reference code-review-graph
```

The second command is optional: it verifies pinned source hashes and compares
normalized nodes, resolved calls, flows, communities and a query on a small
authored fixture. Ordinary tests use the checked-in golden and skip the checkout
comparison. This is structural regression coverage, not proof of all-language
parity or improved issue resolution. Phase F is not implemented.

Use an actual Git root with a committed base; nested folders that resolve to an
ancestor Git repository are rejected. Sage does not initialize fixtures. Parser
identity changed, so the next build reparses existing indexes safely.

```bash
make legion-memory REPO=/absolute/repo
make legion-retrieve REPO=/absolute/repo ISSUE=/absolute/issue.md MEMORY=/absolute/graph.sqlite3
make legion-solve REPO=/absolute/repo ISSUE=/absolute/issue.md MEMORY=/absolute/graph.sqlite3
```

Retrieval saves `graph.context.md` and `graph.retrieval.json` beside
`graph.sqlite3`. JSON includes bounded candidate/channel ranks, reasons,
selection/omission diagnostics, unresolved-edge counts and timing. Re-running
retrieval replaces these latest-result files; per-run evidence remains in
`legion-memory.json` under the run directory.

The solve packet defaults to 4,000 characters; change
`SAGE_LEGION_INITIAL_CONTEXT_CHARS` (500–12,000) deliberately. Offline size
checks cover 500/1,500/4,000/12,000 characters and retain explicit behavior
owners on the regression fixture. This is a provisional compact default, not
a quality-optimal budget established on larger repos. Standalone retrieval's
`--max-chars` is independent. Normal solving exposes five graph tools instead
of all 21; no-match exposes none. Repairs receive fresh unchanged locators;
edited locations require current source reads.

Interpret the final exposure fields literally:

- `available=yes, retrieved=no`: ready graph, no useful initial match; normal inspection.
- `retrieved=yes, exposed=yes, queried=no`: initial context reached the model; no native query needed.
- `read_enriched=yes`: graph facts accompanied source reads/searches.
- `unavailable`: logged fallback, not an assertion that the agent cannot solve.

Path overlap is observational, not proof that memory caused a better fix.
Character counts are serialized/body sizes, not token estimates. Schema sizes
sum bindings, not every model request. Provider token totals remain authoritative;
embedding usage and build cost stay separate. No savings claim has been measured.

For larger indexes, review capacity/cost before enabling hosted work:

```dotenv
SAGE_LEGION_EMBEDDING_MAX_NODES=10000
SAGE_LEGION_EMBEDDING_DEADLINE_SECONDS=1800
SAGE_LEGION_EMBEDDING_CONCURRENCY=4
SAGE_LEGION_EMBEDDING_RETRIES=1
```

Then use `make legion-memory REPO=... EMBEDDINGS=on`. Defaults remain 2,000
nodes, 300 seconds and concurrency 1. Counts/limits are logged before requests;
no arbitrary prefix is published when over capacity. Retry an interrupted build
to reuse acknowledged 32-node checkpoints. A partly completed batch may need
re-embedding. Concurrency does not bypass rate limits. Separate requests avoid
Gemini Embedding 2's multi-input aggregation
([Google embedding contract](https://ai.google.dev/gemini-api/docs/embeddings)).

To catch missing Python tooling before spending model calls, configure the
same opt-in preflight for both `make solve` and `make legion-solve`:

```dotenv
SAGE_VERIFICATION_PREFLIGHT=true
SAGE_VERIFICATION_COMMANDS_JSON=[{"id":"tests","command":"python3 -m pytest -q","required":true}]
SAGE_VERIFICATION_PREFLIGHT_DEPENDENCIES_JSON=["flask","pymongo"]
```

Use your prepared `SANDBOX_IMAGE`. A failed probe stops before model/embedding
calls and writes `verification-preflight.json`; sandbox cleanup still runs.
This checks interpreter/distribution availability only, not test collection,
application imports, dependency compatibility or correctness. Supported commands
are `python[3] -m pytest` and `pytest` (the latter probes Python 3 tooling).
Other runners are rejected when preflight is enabled. No packages are installed
and networking remains disabled. Keep preflight settings identical between arms.

The new tests cover semantic-only matches, no-hit and provider failure fallback,
unchanged-vector reuse, changed/deleted symbols, interrupted-batch recovery,
model-recipe isolation, locks, schema migration, native-tool usage, Gemini
request shape, invalid vectors, and selected parser/flow/community behavior.
Real Gemini and Qdrant-server checks are separate opt-in checks. The offline
suite covers deterministic shared-store generation isolation and retention,
but does not certify hosted-service quotas, availability, or latency.

### Read/search enrichment and expanded native tools

No additional environment variables are required. Memory-enabled Solver runs
automatically enrich `read_file` and `search_text` with bounded callers, callees,
flows, community and test links. No-memory `make solve` is unchanged. Enrichment
does not embed queries: it uses the existing graph's lexical index and respects
requested read ranges, the tool-output cap, and duplicate suppression. Source
output is preserved if memory is missing, stale, busy or unusable.

During a `make legion-solve` run, look for:

```text
Legion Memory enrichment: used; tool=read_file; symbols=2
Legion Memory enrichment: skipped; tool=read_file; symbols=0
```

`skipped` covers no new context or exhausted character budget; `unavailable`
means the graph could not be queried. The final summary reports enrichments
used/attempted. `legion-memory.json` keeps separate `enrichments` records with
status, paths, hits and duration; these are not added to model-requested tool
counts. Repeated reads need not repeat already supplied structural context.

The memory toolset now has 21 read-only tools. New capabilities are
`detect_changes_tool`, `get_review_context_tool`, `find_large_functions_tool`,
`get_surprising_connections_tool`, `get_suggested_questions_tool` and
`refactor_tool`. Review snippets use Sage's existing current-source reader;
refactoring returns candidates/previews only and cannot mutate or issue apply
tokens. The graph's impact/risk estimates never replace verification or review.

All 16 query-pattern names are supported, including `references_to`,
`triggers_of`, `triggered_by`, `publishers_of`, `listeners_of`, `handlers_of`,
`endpoints_for` and `consumers_of`. Ambiguous symbol names return qualified-name
candidates. Empty results explicitly state static-analysis limitations. Config
keys and events can be queried as `config:DB_HOST` / `event::ordered`; querying
`consumers_of` also accepts the raw key. Unsupported dynamic relationships are
not fabricated to produce a positive result.

After changing or deleting a route, event publisher/listener, or config consumer,
commit the fixture change and run `make legion-memory` again. Queries should
lose removed handlers/consumers; shared event/config nodes should move to a
surviving owner or disappear when no references remain. With embeddings enabled,
obsolete-generation points should be removed only after the replacement is ready.

To inspect the new capabilities without a model call, build the memory first,
then adapt these paths:

```bash
uv run --project apps/agent python - /absolute/repo /absolute/memory/graph.sqlite3 <<'PY'
import json
import sys
from pathlib import Path
from sage.legion_memory.service import LegionMemoryService

memory = LegionMemoryService()
scope = dict(repo_root=Path(sys.argv[1]), memory_file=Path(sys.argv[2]))
print(json.dumps(memory.find_large_functions_tool(min_lines=30, **scope), indent=2))
print(json.dumps(memory.get_suggested_questions_tool(**scope), indent=2))
print(json.dumps(memory.detect_changes_tool(**scope), indent=2))
# Replace target with an actual symbol or qualified_name from your graph.
print(json.dumps(memory.query_graph_tool(pattern="references_to", target="checkout", **scope), indent=2))
PY
```

Focused offline regressions:

```bash
uv run --project apps/agent pytest apps/agent/tests/legion_memory/test_remaining.py apps/agent/tests/legion_memory/test_vectors.py
make check
```

The static-pattern matrix includes Python aliases/re-exports/local imports,
inheritance and shadowing; JS/TS imports, JSON tsconfig aliases, callbacks,
routes and events; Java typed receivers and package-isolated Spring events;
Go receivers and Rust static/typed calls. These fixtures are not certification
of every upstream language/framework resolver. JSONC/extended tsconfigs and
arbitrary dynamic dispatch may remain unresolved. Community refinements are
separate from this update. Live comparative evaluation is intentionally left
to the user; no paid evaluation commands were run.

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

### Install GitHub Legion Memory

Accepted GitHub `/sage solve` and `/sage fix` runs build a new Tree-sitter
`graph.sqlite3` under runner temporary storage. The graph is rebuilt at the
accepted base SHA for every run attempt and is not cached or uploaded. GitHub
embeddings default on and use persistent remote Qdrant; local `make solve`
behavior remains memory-free.

Before installing the pinned workflow, create these repository secrets (or
environment secrets when the solve job is assigned to that environment):

```text
SAGE_LEGION_QDRANT_URL          # required HTTPS endpoint
SAGE_LEGION_QDRANT_API_KEY      # required remote credential
SAGE_LEGION_EMBEDDINGS_ENABLED  # optional; set false for lexical memory
```

`GEMINI_API_KEY` remains required for the Reviewer and supplies the existing
Gemini Embedding 2 adapter. Qdrant and model credentials are scoped only to the
trusted solve controller step. They do not enter checkout, dependency install,
Docker build, sandbox, gate, finalizer, prompt, or diagnostic upload steps.

Run the offline policy and controller checks after changing the installation:

```bash
make actions-check
make github-test
make github-doctor
```

`github-doctor` validates the workflow wiring and default without reading
secret values. Missing or invalid remote Qdrant configuration fails before the
first model or embedding call when embeddings are enabled. Setting
`SAGE_LEGION_EMBEDDINGS_ENABLED=false` skips Qdrant and embedding construction
but still builds and uses lexical Legion Memory.

Classify a fixture with no API or model call:

```bash
make github-event-check \
  EVENT=apps/agent/tests/fixtures/github/issue_solve.json
```

The installed workflow accepts exact `/sage solve` and `/sage fix` Issue
comments, rejects pull-request comments, rechecks authorization and duplicate
state before constructing model dependencies, and runs at the gate's exact
base SHA.

After the implementation commit is pushed and the workflow's two Sage Action
references are pinned to its full immutable SHA, use a disposable repository
for one controlled canary. Choose an Issue that names a known symbol or path so
semantic or hybrid retrieval should produce a positive match:

1. invoke one bounded Issue with `/sage solve`;
2. confirm authorization, exact base SHA, and one status-comment lifecycle;
3. inspect uploaded `legion-memory.json` and confirm `build_type=full`, its
   `indexed_sha` equals the GitHub base SHA, vectors are `ready`, Qdrant
   operations are non-zero, retrieval includes `semantic` or `hybrid`, and
   memory exposure or native memory-tool use is recorded;
4. confirm a creation-only `sage/issue-<number>` branch and draft PR;
5. confirm the uploaded diagnostic allowlist contains no checkout, rendered
   memory context, Issue body, Qdrant endpoint, or credentials;
6. rerun finalization and confirm it is idempotent; and
7. clean up through normal repository maintenance, not through Sage.

A second bounded Issue started while the default branch remains at the same
exact base should report zero new document embeddings and reuse all eligible
vectors. For a lexical-only smoke, temporarily set the optional embedding secret
to `false`, invoke a fresh bounded Issue, and confirm the graph remains present
while vector status is disabled. Restore the intended secret afterward.

The canary cannot be run until the implementation commits are pushed and an
immutable implementation SHA is pinned.

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
directory and remain a fixed allowlist in the workflow. Their
`legion-memory.json` copy removes the rendered retrieval context while retaining
SHA, counts, timings, vector status, search modes, exposure, and usage evidence.

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

GitHub embedding configuration failure: confirm both Qdrant secrets exist, the
URL uses HTTPS without embedded credentials or query parameters, and
`SAGE_LEGION_QDRANT_PATH` is not configured in the solve environment. To keep
lexical memory available during Qdrant maintenance, set the optional embedding
secret to `false` rather than removing the memory integration.

GitHub vector fallback with valid configuration: inspect the redacted
`legion-memory.json` vector reason and Qdrant/embedding usage counts. A transient
Gemini or Qdrant failure preserves the fresh graph and lexical retrieval, but a
release canary is not successful until vector status is `ready`.

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
