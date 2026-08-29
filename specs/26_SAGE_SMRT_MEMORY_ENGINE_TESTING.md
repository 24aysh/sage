# Sage SMRT Memory Engine: Local Testing Guide

## Purpose and safety rules

This guide verifies the opt-in SMRT memory engine without making it the
default. Start with offline tests, use the named disposable PostgreSQL service
for integration tests, and only then consider a live Neon canary.

Keep these rules throughout:

- Never put a real PostgreSQL DSN, API token, or private source in a command,
  shell history, test fixture, screenshot, Issue comment, or Git-tracked file.
- Load real credentials from an approved secret manager or a local ignored file
  with mode `0600`. Do not print the file or inspect the environment with broad
  commands such as `env`.
- Use a pooled Neon DSN for `SAGE_MEMORY_DATABASE_URL` and a direct Neon DSN for
  `SAGE_MEMORY_MIGRATION_DATABASE_URL`.
- The Docker test credentials in this guide are deliberately local and
  disposable. Never reuse them outside the named test container.
- Leave `LANGSMITH_TRACING=false` unless sending Issue and repository context to
  LangSmith has been explicitly approved.

## Quick start: local solve with Docker PostgreSQL

This is the shortest complete path from a new checkout to one memory-enabled
local solve. Run every command from the Sage repository root.

### A. Install Sage and build the execution sandbox

```bash
make setup
make sandbox-build
make sandbox-smoke
```

Pass: dependency installation completes, `sage-sandbox:v0` is present, and the
network-disabled sandbox smoke test passes.

### B. Start the disposable local PostgreSQL service

```bash
docker compose -p sage-memory-test \
  -f apps/agent/tests/memory/docker-compose.yml up -d --wait
```

The service listens only on `127.0.0.1:55432` and uses the named disposable
database `sage_memory_test`. Confirm it is healthy without printing secrets:

```bash
docker compose -p sage-memory-test \
  -f apps/agent/tests/memory/docker-compose.yml ps
```

### C. Create base and memory environment files

Keep normal model/runtime configuration in `.env` and memory/PostgreSQL
configuration in a separate overlay:

```bash
make env
cp .env.memory.local.example .env.memory.local
chmod 600 .env
chmod 600 .env.memory.local
```

Open `.env` and configure the normal Sage settings, including:

```dotenv
OPENAI_API_KEY=<your Solver API key>
GEMINI_API_KEY=<your Reviewer and memory summarizer API key>

SAGE_RUNTIME=v2
SAGE_MODEL_PROFILE=constrained-cross-provider
SAGE_GOOGLE_MODEL_CONTEXT_APPROVED=true
SAGE_V2_SOLVER_MODEL=gpt-5.4-mini
SAGE_V2_REVIEWER_MODEL=gemini-3.5-flash

SAGE_RESEARCH_ENABLED=false
LANGSMITH_TRACING=false
```

Then open `.env.memory.local`. It already contains the disposable local Docker
DSNs and the remaining memory defaults:

```dotenv
SAGE_MEMORY_ENABLED=true
SAGE_MEMORY_DATABASE_URL=postgresql://sage_test:sage_test@127.0.0.1:55432/sage_memory_test
SAGE_MEMORY_MIGRATION_DATABASE_URL=postgresql://sage_test:sage_test@127.0.0.1:55432/sage_memory_test
SAGE_MEMORY_REPOSITORY_KEY=local-demo-repository-v1
SAGE_MEMORY_SUMMARIZER_PROVIDER=google
SAGE_MEMORY_SUMMARIZER_MODEL=gemini-3.5-flash
```

The PostgreSQL credentials above are only for the disposable local Docker
service. Never use them for Neon or another environment. Choose one stable,
non-secret `SAGE_MEMORY_REPOSITORY_KEY` per target repository and reuse it for
warm runs. Do not use the same key for unrelated repositories.

Confirm the file is ignored:

```bash
git check-ignore -v .env
git check-ignore -v .env.memory.local
```

Pass: Git reports an ignore rule for both private files. The tracked
`.env.memory.local.example` must not be ignored.

### D. Migrate and diagnose memory

```bash
make memory-migrate ENV_FILE=.env.memory.local
make memory-doctor ENV_FILE=.env.memory.local
make memory-test
```

Expected migration output:

```text
Memory migration ready: 0001_smrt_v1
```

Expected doctor results include `postgres: ok`, `fts5: ok`, and parsed Python,
JavaScript, and TypeScript grammars. `make memory-test` is offline and skips the
opt-in PostgreSQL-marked test.

Optionally run the destructive disposable-database integration test before
creating memory you want to inspect:

```bash
export SAGE_MEMORY_TEST_DATABASE_URL='postgresql://sage_test:sage_test@127.0.0.1:55432/sage_memory_test'
make memory-postgres-test
unset SAGE_MEMORY_TEST_DATABASE_URL
```

That test drops and recreates only the `sage_smrt` schema in
`sage_memory_test`. Running it later will erase manual snapshots in this
disposable database.

### E. Create the local Issue input

Use a committed Git repository as the target. Uncommitted target-repository
changes are intentionally absent because Sage clones the selected commit.

```bash
make new-issue ISSUE=/tmp/sage-local-issue.md
```

Edit `/tmp/sage-local-issue.md` into one concrete task with acceptance criteria.
Keeping the Issue file outside the target repository prevents it from becoming
part of the candidate checkout.

### F. Run the Issue through the Makefile

```bash
set -a
source .env.memory.local
set +a
make solve \
  ENV_FILE=.env \
  REPO=/absolute/path/to/target-git-repository \
  ISSUE=/tmp/sage-local-issue.md \
  BASE_REF=HEAD
```

This is a live run: the Solver, Reviewer, and memory summarizer can make paid
provider calls. Sage works in an isolated clone under `.sage/runs`; it does not
edit the original target checkout. A successful no-change result is reported
as a warning by Make rather than a Make failure.

For detailed local logs:

```bash
set -a
source .env.memory.local
set +a
make solve-debug \
  ENV_FILE=.env \
  REPO=/absolute/path/to/target-git-repository \
  ISSUE=/tmp/sage-local-issue.md \
  BASE_REF=HEAD
```

Do not share debug logs until they have been reviewed for repository content.

Memory lifecycle is visible at `INFO` level. An enabled healthy run reports
that PostgreSQL is accessible, the input snapshot (or `none` on a cold start),
the number of prior documents loaded, the active mode, the paths supplied to
the Solver, and the snapshot publication result. Disabled and fallback modes
are logged explicitly. For example:

```text
memory PostgreSQL accessible ... input_snapshot_id=<uuid> prior_documents=9
memory startup completed ... mode=healthy
memory context supplied to solver ... files=5 ... paths=[...]
memory finalized ... snapshot_published=True ... reused_cards=7 created_cards=3
```

`make solve-debug` additionally reports bounded access details:

```text
memory initial source supplied to solver coverage=[{'path': 'src/example.py', 'lines': ((1, 80),), 'chars': 2400}]
memory context materialized path='src/example.py' ... lines=1-80 chars=2400
memory expansion supplied to agent files=1 coverage=[...]
memory source read access=read_file mode=healthy path='src/example.py' lines=1-40
memory tree accessed path='src' max_depth=1 truncated=False
memory text search accessed scope='tests' query_chars=12 matches=[('tests/test_example.py', 8)]
```

These messages identify repository paths and line ranges supplied through the
context or returned by repository tools. They deliberately do not print source
text, search text, Issue text, semantic payloads, database URLs, credentials,
or raw exception messages. "Supplied to solver" records the controller/model
boundary; it cannot prove which tokens the model internally attended to.

### G. Locate and validate the run

Copy the run path printed by Sage, or select the newest sortable run ID:

```bash
RUN_DIR="$(find .sage/runs -mindepth 1 -maxdepth 1 -type d | sort | tail -1)"
test -n "$RUN_DIR"
printf '%s\n' "$RUN_DIR"
make run-status RUN_DIR="$RUN_DIR"
```

List available memory artifacts:

```bash
find "$RUN_DIR" -maxdepth 2 -type f \
  \( -name 'memory-summary.json' -o \
     -name 'context-forest.json' -o \
     -path '*/context-expansions/*.json' \) -print | sort
```

An enabled run always writes `context-forest.json` and
`memory-summary.json`. `context-expansions/NN.json` exists only when the Solver
requested semantic expansion.

### H. Inspect run-local memory artifacts safely

`memory-summary.json` is bounded and contains no source payload. Pretty-print
it directly:

```bash
uv run --project apps/agent python -m json.tool \
  "$RUN_DIR/memory-summary.json"
```

For a healthy run, check:

- `mode` is `healthy`;
- `snapshot_published` is `true`;
- `output_snapshot_id` is present;
- `failure` is null;
- summarizer and reuse counters are plausible; and
- `retained_snapshot_count` is between one and five.

Print context provenance without printing source excerpts:

```bash
uv run --project apps/agent python - "$RUN_DIR/context-forest.json" <<'PY'
import json
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
for entry in payload.get("entries", []):
    print({
        "path": entry["path"],
        "role": entry["role"],
        "added_by": entry["added_by"],
        "reason": entry["reason"],
        "evidence_tier": entry["evidence_tier"],
        "materialization": entry["materialization"],
        "line_ranges": entry["included_line_ranges"],
        "source_chars": len(entry.get("source") or ""),
    })
PY
```

To intentionally inspect the raw context sent to the Solver, use:

```bash
uv run --project apps/agent python -m json.tool \
  "$RUN_DIR/context-forest.json"
```

This prints private committed/workspace source. Do not paste it into an Issue,
chat, CI log, or diagnostic upload. Expansion artifacts have the same privacy
classification and can be inspected with `python -m json.tool` individually.

The other useful run outputs are:

```bash
uv run --project apps/agent python -m json.tool "$RUN_DIR/usage.json"
uv run --project apps/agent python -m json.tool "$RUN_DIR/terminal.json"
git -C "$RUN_DIR/repo" status --short
git -C "$RUN_DIR/repo" diff --stat HEAD --
```

### I. Inspect canonical PostgreSQL memory

The supported bounded inspection command does not print semantic payloads:

```bash
set -a
source .env.memory.local
set +a
uv run --project apps/agent sage memory inspect \
  --namespace-kind local \
  --repository-key "$SAGE_MEMORY_REPOSITORY_KEY" \
  --display-name local-demo
```

Expected fields include `found: True`, `ready_snapshots`, `latest_target`, and
`semantic_objects`. `ready_snapshots` never exceeds five.

For this disposable local database only, inspect canonical rows manually:

```bash
docker compose -p sage-memory-test \
  -f apps/agent/tests/memory/docker-compose.yml exec -T postgres \
  psql -U sage_test -d sage_memory_test -c \
  "SELECT namespace_kind, namespace_key, display_name, latest_ready_snapshot_id FROM sage_smrt.repositories ORDER BY namespace_key;"
```

Snapshot history and retention:

```bash
docker compose -p sage-memory-test \
  -f apps/agent/tests/memory/docker-compose.yml exec -T postgres \
  psql -U sage_test -d sage_memory_test -c \
  "SELECT r.namespace_key, s.status, left(s.target_commit_oid, 12) AS target, s.created_at, s.ready_at FROM sage_smrt.snapshots s JOIN sage_smrt.repositories r USING (repository_id) ORDER BY s.created_at DESC;"
```

Card counts and refresh modes:

```bash
docker compose -p sage-memory-test \
  -f apps/agent/tests/memory/docker-compose.yml exec -T postgres \
  psql -U sage_test -d sage_memory_test -c \
  "SELECT r.namespace_key, o.node_type, o.generation_mode, o.delta_depth, count(*) FROM sage_smrt.semantic_objects o JOIN sage_smrt.repositories r USING (repository_id) GROUP BY r.namespace_key, o.node_type, o.generation_mode, o.delta_depth ORDER BY r.namespace_key, o.node_type;"
```

Inspect bounded semantic summaries. These are model-generated descriptions;
PostgreSQL does not contain raw source blobs:

```bash
docker compose -p sage-memory-test \
  -f apps/agent/tests/memory/docker-compose.yml exec -T postgres \
  psql -U sage_test -d sage_memory_test -c \
  "SELECT r.namespace_key, o.node_type, left(o.source_oid, 12) AS source_oid, left(o.semantic_digest, 12) AS semantic_digest, o.semantic_payload->>'summary' AS summary FROM sage_smrt.semantic_objects o JOIN sage_smrt.repositories r USING (repository_id) ORDER BY o.created_at DESC LIMIT 50;"
```

Inspect valid/stale/missing overlay state without dumping payloads:

```bash
docker compose -p sage-memory-test \
  -f apps/agent/tests/memory/docker-compose.yml exec -T postgres \
  psql -U sage_test -d sage_memory_test -c \
  "SELECT r.namespace_key, o.node_type, o.semantic_state, o.coverage_state, count(*) FROM sage_smrt.overlay_nodes o JOIN sage_smrt.repositories r USING (repository_id) GROUP BY r.namespace_key, o.node_type, o.semantic_state, o.coverage_state ORDER BY r.namespace_key, o.node_type;"
```

There is no persistent SQLite memory file to inspect: FTS5 is derived in memory
for one solve and closed afterward. Canonical cards and snapshot roots live in
PostgreSQL; current raw source/excerpts live only in the isolated run workspace
and context artifacts.

### J. Verify warm reuse

Run the same command again against the same committed target and with the same
`SAGE_MEMORY_REPOSITORY_KEY`. Inspect the new `memory-summary.json`.

Pass: `input_snapshot_id` references the prior publication and
`reused_cards` increases when unchanged learned blobs/directories are revisited.
The second run may still create cards for newly explored paths.

### K. Stop or destroy the local database

Stop the service while retaining its named volume and warm memory:

```bash
docker compose -p sage-memory-test \
  -f apps/agent/tests/memory/docker-compose.yml stop
```

Restart it later with the command from step B. To permanently remove the
disposable local database, container, network, and named memory volume:

```bash
docker compose -p sage-memory-test \
  -f apps/agent/tests/memory/docker-compose.yml down -v
```

`down -v` irreversibly deletes all snapshots/cards in this disposable service.
It does not delete `.sage/runs` artifacts.

## 1. Prerequisites

From the repository root, confirm:

```bash
git --version
uv --version
docker --version
docker compose version
python3 --version
```

Pass criteria:

- Git, `uv`, Docker, and Docker Compose exit `0`.
- Docker Desktop/Engine is running.
- `uv` can provision the project's Python 3.14 environment.
- For a live solve, the `sage-sandbox:v0` image exists or can be built with
  `make sandbox-build`.

Install the locked environment:

```bash
make setup
```

Pass: `uv` finishes without changing dependencies beyond the committed lock.
Fail: wheel/ABI resolution errors, especially for `tree-sitter` grammars, must
be resolved before continuing.

## 2. Protect local memory configuration

Create a non-tracked, owner-only memory overlay from the tracked template:

```bash
cp .env.memory.local.example .env.memory.local
chmod 600 .env.memory.local
```

Edit it through an approved secret manager or editor. Do not paste a real DSN
or token into the terminal. The important names are:

```dotenv
SAGE_MEMORY_ENABLED=true
SAGE_MEMORY_DATABASE_URL=<pooled runtime DSN>
SAGE_MEMORY_MIGRATION_DATABASE_URL=<direct migration DSN>
SAGE_MEMORY_REPOSITORY_KEY=<stable opaque local key>
SAGE_MEMORY_SUMMARIZER_PROVIDER=google
SAGE_MEMORY_SUMMARIZER_MODEL=gemini-3.5-flash
```

For live solves, keep the normal V2 provider variables and API keys in `.env`,
created from `.env.example`. The angle-bracket values above are labels, not
usable secrets. Confirm that the local overlay is ignored before putting any
real value in it:

```bash
git check-ignore -v .env.memory.local
```

Pass: Git reports an ignore rule. Fail: if it is not ignored, stop and add an
appropriate local-only ignore rule without committing the secret file.

Load base configuration followed by the memory overlay without printing either:

```bash
set -a
source .env
source .env.memory.local
set +a
```

## 3. Migrate a fresh PostgreSQL or Neon database

Migration is explicit; `sage solve` never performs DDL. With the direct DSN
already loaded from the protected configuration file, run:

```bash
make memory-migrate ENV_FILE=.env.memory.local
```

Expected output:

```text
Memory migration ready: 0001_smrt_v1
```

Run the command a second time. It must return the same version without changing
the schema. A changed checksum for an applied migration is a hard failure.

Pass: both runs exit `0` and print only the version. Fail: output contains a DSN,
SQL parameters, credentials, or a different migration result.

For Neon, use the direct (non-`-pooler`) endpoint only for this command. Runtime
solves should use the pooled endpoint because GitHub jobs are short lived.

## 4. Run `memory doctor`

Offline/local capability check:

```bash
SAGE_MEMORY_ENABLED=false SAGE_MEMORY_DATABASE_URL= \
  uv run --project apps/agent sage memory doctor
```

Expected important lines:

```text
fts5: ok
postgres: not_configured
tree_sitter: {'python': 'parsed', 'javascript': 'parsed', 'typescript': 'parsed'}
```

With protected live configuration loaded:

```bash
make memory-doctor ENV_FILE=.env.memory.local
```

Pass: `postgres: ok`, `runtime_schema_access: True`, `fts5: ok`, and all three
grammars report `parsed`. The command does not call a model. Fail: any secret is
printed, the schema is outdated, or a required local capability is unavailable.

## 5. Prove disabled/omitted mode does no memory work

Run the focused configuration and disabled-runtime tests:

```bash
uv run --project apps/agent pytest -c apps/agent/pyproject.toml \
  apps/agent/tests/test_config.py \
  apps/agent/tests/runtimes/v2/test_runtime.py
```

Then run any normal local solve with `SAGE_MEMORY_ENABLED=false` or with the
variable omitted. In its run directory, check only the file names:

```bash
find .sage/runs -name memory-summary.json -o -name context-forest.json
```

Pass: the tests pass and a disabled solve creates neither memory artifact. It
must not require either memory DSN or a local repository key. Fail: any database
connection, memory summarizer call, or memory artifact occurs in disabled mode.

## 6. Run the offline memory suite

```bash
make memory-test
```

This suite uses temporary Git repositories, in-memory SQLite FTS5, fake
summarizers/stores, and strict models. It performs no paid model or network call.

Pass: all offline tests pass and the PostgreSQL-marked test is skipped. Fail:
any live credential is requested or any test depends on existing developer
memory state.

For all deterministic backend checks:

```bash
make check
make actions-check
```

Pass: the unit suite, workflow policy checks, and Python compilation all pass.

## 7. Start the named local PostgreSQL integration service

Start only the memory test service:

```bash
docker compose -p sage-memory-test \
  -f apps/agent/tests/memory/docker-compose.yml up -d --wait
```

Set the fixed, disposable local test DSN:

```bash
export SAGE_MEMORY_TEST_DATABASE_URL='postgresql://sage_test:sage_test@127.0.0.1:55432/sage_memory_test'
```

Run integration tests:

```bash
make memory-postgres-test
```

The test refuses a DSN that does not name `sage_memory_test`. It drops only the
`sage_smrt` schema in that disposable database and verifies migration
idempotency, immutable semantic collisions, dependency hydration, atomic
publication, repository isolation, five-root retention, and digest integrity.

Pass: the marked integration test runs rather than skips and exits `0`. Fail:
the command targets any shared/development/production database.

Remove only this named service and its named volume:

```bash
docker compose -p sage-memory-test \
  -f apps/agent/tests/memory/docker-compose.yml down -v
unset SAGE_MEMORY_TEST_DATABASE_URL
```

## 8. Cold local solve walkthrough

Only do this after offline and PostgreSQL checks pass. It invokes the Solver,
Reviewer, and possibly the memory summarizer, so it can incur model cost and
send committed source to the configured providers.

Choose a committed test repository and an Issue file outside that repository.
With protected configuration loaded, run:

```bash
LANGSMITH_TRACING=false make solve \
  ENV_FILE=.env \
  REPO=/absolute/path/to/test-repository \
  ISSUE=/absolute/path/to/test-issue.md \
  BASE_REF=HEAD
```

Inspect the newest run without printing raw context:

```bash
run_dir=$(find .sage/runs -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' \
  | sort -nr | head -1 | cut -d' ' -f2-)
uv run --project apps/agent python -c \
  "import json,sys; p=json.load(open(sys.argv[1])); print({k:v for k,v in p.items() if k != 'failure'})" \
  "$run_dir/memory-summary.json"
```

Pass for a cold run:

- `mode` is `healthy`;
- `input_snapshot_id` may be null;
- `snapshot_published` is true;
- `output_snapshot_id` is present;
- only files selected/materialized by the run contribute cards; and
- candidate worktree changes do not appear as base memory.

If `mode` is `fallback`, the Issue solve may still succeed; diagnose the safe
failure fields before treating memory as healthy.

## 9. Warm repeat and reuse

Run the same committed target and a comparable Issue again with the same stable
repository key. Inspect the new `memory-summary.json` as above.

Pass: `input_snapshot_id` equals a prior output snapshot, `reused_cards` is
non-zero when the same known blobs are revisited, and created-card/model work is
not greater without a source change. Retrieval remains deterministic for tied
evidence. Fail: unchanged files are summarized again without a version/config
change or memory crosses repository keys.

## 10. Changed file and rename catch-up

In the disposable target repository:

1. commit one change to a previously learned file;
2. run a new Issue solve at that commit;
3. commit a pure `git mv` of another learned file;
4. run again with the same repository key.

Expected behavior:

- changed known blob: the old card is only a stale refresh hint until current
  committed source is re-read and summarized;
- unchanged blob: its semantic object is reused;
- unambiguous pure rename: semantic content is reused while ancestor overlay
  digests/path edges change;
- changed unknown file: no card is created merely because Git changed it; and
- candidate files created during a solve never enter that base snapshot.

Pass: counters and snapshots follow those rules. Fail: stale content is supplied
to the Solver as current source.

## 11. Forced fallback checks

The deterministic failure tests are safe and free:

```bash
uv run --project apps/agent pytest -c apps/agent/pyproject.toml \
  apps/agent/tests/memory/test_failure.py \
  apps/agent/tests/memory/test_memory_context.py
```

For a disposable manual DB fallback canary, stop the named PostgreSQL test
container after startup, keep memory enabled against only that disposable DSN,
and start a solve. Do not use this procedure with a live Neon DSN.

Expected: startup transitions once to `fallback`; ordinary tree/search/read
tools remain available; no later memory adapter operation occurs; the solve is
not failed merely because memory is unavailable; and `memory-summary.json`
contains only a safe component, stage, code/message, target, and fallback action.

FTS5, parser, and summarizer failures are injected with fakes in the offline
suite. Do not induce a paid-provider failure merely to test fallback.

## 12. Inspect artifacts and context provenance

For an enabled attempt, the local run may contain:

```text
memory-summary.json
context-forest.json
context-expansions/NN.json
```

`context-forest.json` contains repository paths and raw source excerpts. Treat
it as private repository data. It is deliberately excluded from GitHub's
diagnostic upload allowlist. `memory-summary.json` contains counters and safe
identifiers only and is allowlisted.

Every context entry must have `added_by`, `reason`, `evidence_tier`, a base blob
OID when applicable, a workspace digest, materialization type, and line ranges.
Pass: provenance is complete and bounded. Fail: DSNs, Issue bodies, SQL,
provider response bodies, stack traces, or candidate-only source appear in the
summary.

During a healthy interactive solve, `inspect_context` exposes the same active
path/provenance/read-coverage metadata without returning canonical semantic
cards. `read_file` and `search_text` must reject paths outside that active
forest or a branch exposed by bounded tree navigation.
`materialize_dependency` must require a reason naming an active source path or
a concrete path beneath a directory exposed by bounded tree navigation, while
`expand_context` returns only newly admitted files. Once fallback occurs,
ordinary repository exploration becomes available for the remainder of that
solve.

For a cold repository with no prior snapshot, also verify that an Issue naming
a specific path component (for example, `factorial`) admits the matching Git
inventory path even when its filename is generic (for example,
`project/src/factorial/main.py`). If no initial candidate matches, the Solver
may list `.` only at depth one. That listing authorizes only the directory
branches it reveals; Sage may then descend one bounded listing at a time,
search a revealed directory, and materialize a concrete revealed file. It must
still reject a skipped directory level and a repository-wide text search.

## 13. Verify five-snapshot retention

After at least six healthy publications for one repository identity, run:

```bash
uv run --project apps/agent sage memory inspect \
  --namespace-kind local \
  --repository-key "$SAGE_MEMORY_REPOSITORY_KEY" \
  --display-name local-canary
```

Expected important line:

```text
ready_snapshots: 5
```

Before six publications, the value must be between one and five. `inspect`
never prints semantic payloads. Pass: exactly five READY roots remain after the
sixth publication. Fail: BUILDING/FAILED roots become latest or more than five
READY roots remain.

## 14. Two-Issue GitHub queue canary

On a non-production canary repository:

1. keep `SAGE_MEMORY_ENABLED=false` for the first queue test;
2. open two small independent Issues;
3. post `/sage solve` on both close together;
4. observe the `solve` jobs, not merely the workflow runs; and
5. repeat in another canary repository if cross-repository concurrency matters.

Pass:

- the gate jobs can finish independently;
- both status comments say the request was accepted/queued;
- only one `solve` job runs at a time for one numeric repository ID;
- the later solve resolves the then-current default-branch target after waiting;
- different repositories can run solve jobs concurrently; and
- neither run is cancelled when the next request arrives.

GitHub does not guarantee an exact queue position or strict FIFO ordering. The
queue is capped by GitHub's pending-run and waiting-time limits.

## 15. Draft PR fallback section

Use a disposable canary configuration that causes the memory startup fallback
described in section 11 while allowing the normal solve to finish. Inspect the
draft PR body.

Pass: it contains `## Sage Memory`, `Status: fallback`, safe component/stage/error,
snapshot or `unavailable`, target commit, and `full repository exploration`.
It must not contain the DSN, SQL, raw source, provider payload, or traceback.
Healthy and disabled runs need no memory section.

## 16. Command result checklist

| Command | Passing result | Common failure meaning |
| --- | --- | --- |
| `make setup` | locked environment installed | dependency/wheel/network issue |
| `make memory-test` | offline tests pass; DB test skipped | core model/retrieval/policy defect |
| `make memory-migrate` | version `0001_smrt_v1` | direct DSN/schema permission/checksum issue |
| `make memory-doctor` | DB, FTS5, grammars are `ok` | pooled DSN/schema/ABI/runtime issue |
| `make memory-postgres-test` | marked DB test runs and passes | disposable DB or migration defect |
| `make actions-check` | queue/secret/allowlist tests pass | unsafe workflow wiring |
| `sage memory inspect ...` | bounded counts, at most five READY | publication/retention defect |

## 17. Cost and privacy warnings

- Offline tests, migration, doctor, and inspect make no model calls.
- A live memory-enabled solve may send committed file source and validated child
  semantic cards to the dedicated summarizer, plus normal Issue/repository
  context to the V2 Solver and Reviewer.
- Tracing can copy prompts, source, outputs, and metadata to a third party.
- Confirm provider retention policy, Neon region/data residency, private-source
  policy, and budget before a live canary.
- Never upload `context-forest.json`, FTS databases, semantic payload dumps, or
  protected configuration files as diagnostics.

## 18. Troubleshooting

### `schema_outdated` or checksum failure

Use the direct migration endpoint and rerun `make memory-migrate`. Never edit an
already-applied SQL migration. Add a new numbered migration instead.

### `fts5: unavailable`

The Python `sqlite3` build lacks FTS5. Use the project-supported Python build;
do not add a second SQLite package as a workaround. Memory must fall back safely.

### Tree-sitter reports `incompatible`

Run `make setup` from the committed lock and confirm Python 3.14-compatible
wheels for core plus the Python/JavaScript/TypeScript grammars. Do not upgrade
one grammar independently without rerunning parser fixtures.

### Neon is suspended or slow to wake

Retry `memory doctor` after the compute wakes. Runtime acquisition is bounded;
an unavailable database should cause solve-local fallback, not hang the solve.

### Connection limit or pool exhaustion

Confirm the runtime uses the pooled Neon endpoint, migration uses the direct
endpoint, and no abandoned solve process remains. Each solve pool is bounded to
four connections and closes during runtime finalization.

### Invalid DSN

Correct the value in the protected secret source. Do not print or paste it into
an Issue. The CLI intentionally reports only a safe reachability/migration
failure.

### Memory-enabled local solve requests a repository key

Set one stable opaque `SAGE_MEMORY_REPOSITORY_KEY` in protected configuration.
Do not derive it from a credential-bearing remote URL or a movable absolute
path.
