# GitHub Actions Legion Memory Implementation Plan

## Document status

> **Status:** Implemented in ten signed-off local commits through the offline
> release gates. Push, action pin, and the live canary remain pending explicit
> release authorization and configured credentials.
>
> **Created:** 9 September 2026
>
> **Sage baseline audited:** `2dd56208b0dc` on `legion`
>
> **Depends on:** the implemented local graph, solve-session, Gemini Embedding 2,
> and Qdrant work described in
> [`25_LEGION_MEMORY_IMPLEMENTATION_PLAN.md`](25_LEGION_MEMORY_IMPLEMENTATION_PLAN.md).
>
> **Current architecture source of truth:**
> [`../docs/architecture.md`](../docs/architecture.md)
>
> **Current testing source of truth:**
> [`../docs/testing.md`](../docs/testing.md)

This specification records the GitHub Actions integration for Legion Memory.
A GitHub `/sage solve` or `/sage fix` builds a new Tree-sitter graph
for that solve, persist code embeddings in a remote Qdrant deployment, retrieve
hybrid Issue context, and then use the existing Solver, verification, Reviewer,
and draft-pull-request lifecycle.

The SQLite graph is deliberately disposable. It must not be uploaded, restored
from an Actions cache, or treated as cross-run state. Qdrant is the only durable
Legion storage used by GitHub Actions.

---

## 1. Objective and acceptance outcome

The target flow is:

```text
exact /sage solve Issue comment
  -> existing gate authorizes, deduplicates, and freezes base SHA
  -> solve job checks out that exact SHA
  -> existing solve workflow creates its isolated run workspace
  -> build a fresh graph.sqlite3 from committed source with Tree-sitter
  -> synchronize/reuse that graph's node embeddings through remote Qdrant
  -> retrieve bounded hybrid Issue context
  -> expose memory context and native read-only memory tools to the Solver
  -> run the existing solve -> verify -> review/repair lifecycle
  -> publish the existing creation-only branch and draft PR
  -> upload bounded evidence proving memory configuration and use
```

The implementation is complete only when all of the following are true:

1. Every accepted GitHub solve requests Legion Memory without requiring a new
   command or workflow selector.
2. `graph.sqlite3` is created from scratch for each solve at the gate's exact
   accepted base SHA.
3. The graph remains runner-local and ephemeral; no SQLite file, lock file, or
   local Qdrant directory is cached or uploaded.
4. GitHub solves enable embeddings by default.
5. Setting the repository or environment secret
   `SAGE_LEGION_EMBEDDINGS_ENABLED=false` disables only embeddings. The fresh
   lexical graph and memory tools remain enabled.
6. With embeddings enabled, the solve controller uses only the configured
   remote Qdrant URL and API key. It never silently falls back to local Qdrant.
7. Rebuilding SQLite does not destroy Qdrant reuse: a second solve of the same
   repository/SHA/model/recipe can reuse verified document vectors without a
   Gemini document-embedding call.
8. Concurrent solves for the same repository cannot prune or query each
   other's unpublished or different-SHA vector generations.
9. The existing authorization, exact-SHA, networkless sandbox, verification,
   review, publication, status-comment, and least-privilege boundaries remain
   intact.
10. The workflow is pinned to the immutable implementation commit and one
    controlled `/sage solve` canary produces a draft PR with evidence that the
    accepted-SHA graph was built and memory was exposed or queried.

This plan does not claim that memory improves solve quality or reduces cost.
Those claims still require the evaluation gates in
[`26_LEGION_MEMORY_RETRIEVAL_EFFICIENCY_GAP_PLAN.md`](26_LEGION_MEMORY_RETRIEVAL_EFFICIENCY_GAP_PLAN.md).

---

## 2. Existing implementation to reuse

Do not add a GitHub-specific solve runtime, graph engine, embedding provider, or
Qdrant client. Extend these existing owners:

| Existing owner | Reuse or extension |
| --- | --- |
| [`sage/workflows/solve.py`](../apps/agent/src/sage/workflows/solve.py) | Reuse `_prepare_memory`, the accepted-SHA check, fail-open graph behavior, `MemorySession`, and artifact writes. |
| [`sage/workflows/github.py`](../apps/agent/src/sage/workflows/github.py) | Add trusted, run-scoped memory-file selection and inject the existing solve memory service after repeat authorization. |
| [`sage/domain/solve.py`](../apps/agent/src/sage/domain/solve.py) | Reuse `SolveRequest.memory_file`; do not add a second GitHub request model. |
| [`sage/composition.py`](../apps/agent/src/sage/composition.py) | Remain the only production construction map for the Gemini provider, Qdrant adapter, and `LegionMemoryService`. |
| [`sage/config.py`](../apps/agent/src/sage/config.py) | Reuse typed `LegionEmbeddingSettings` and add a GitHub remote-Qdrant validation boundary. |
| [`sage/legion_memory/service.py`](../apps/agent/src/sage/legion_memory/service.py) | Reuse the one `build_or_update_graph_tool` entry point, parsing, repository identity, SHA validation, and retrieval surface. |
| [`sage/legion_memory/vectors.py`](../apps/agent/src/sage/legion_memory/vectors.py) | Preserve bounded node text, model/recipe fingerprinting, synchronization, readiness publication, and query behavior; change durable identity so it does not depend on an ephemeral SQLite namespace. |
| [`sage/integrations/qdrant.py`](../apps/agent/src/sage/integrations/qdrant.py) | Extend the existing safe adapter rather than importing Qdrant response shapes into workflows or agents. |
| [`sage/integrations/github/diagnostics.py`](../apps/agent/src/sage/integrations/github/diagnostics.py) | Extend the fixed diagnostic allowlist with the existing bounded memory artifact. |
| [`.github/actions/sage-solve/action.yml`](../.github/actions/sage-solve/action.yml) | Add narrowly scoped memory/Qdrant inputs and solve-step environment wiring. |
| [`.github/workflows/sage.yml`](../.github/workflows/sage.yml) | Supply default-on embedding policy and secrets while preserving the three-job gate/solve/finalize structure. |

The graph builder currently runs in the trusted controller process against the
prepared run workspace. Repository commands still run inside the networkless
Docker sandbox. Preserve that separation: remote Qdrant and Gemini calls belong
to the controller, and neither credential may enter the target checkout,
candidate workspace, Docker build, sandbox environment, prompt, or artifact.

---

## 3. Audited gaps that must be resolved first

### 3.1 GitHub solves do not request memory

`run_github_issue` currently creates a `SolveRequest` without `memory_file`, and
the CLI calls `solve_issue` without a `LegionMemoryService`. Consequently the
local memory implementation is never constructed or used by `/sage solve`.

### 3.2 Current Qdrant identity is coupled to disposable SQLite

Schema migration 2 creates a random `memory_namespace` in SQLite. `VectorIndex`
uses that value, the repository ID, and the embedding fingerprint to name its
Qdrant collection. Its reuse manifest is also stored in SQLite's `vector_nodes`
table.

If Actions creates a new SQLite file on every solve without changing this
contract, each run receives a new collection namespace, has no prior manifest,
and re-embeds every eligible node. Old collections also become unreachable.
That would not satisfy durable Qdrant storage.

### 3.3 Current pruning is unsafe for shared Actions writers

The current single-owner lifecycle prunes every generation except the one just
published. GitHub's concurrency group is per Issue, so two Issues in the same
repository may legitimately solve at once. One run must not delete another
run's accepted-SHA generation.

### 3.4 Workflow evidence omits the memory artifact

Local runs write `legion-memory.json`, but the GitHub diagnostic copy and upload
allowlists omit it. The final canary therefore cannot prove graph provenance,
vector readiness, retrieval mode, or Solver exposure from uploaded evidence.

---

## 4. Storage and lifecycle decisions

### 4.1 SQLite graph: fresh for every solve

Use an explicit trusted path under `runner_temp`, for example:

```text
$RUNNER_TEMP/sage-legion-memory/<actions-run-id>-<attempt>/graph.sqlite3
```

The path must be outside the checked-out repository, materialized Issue context,
diagnostics directory, and candidate workspace. It must be unique to the
Actions run attempt and absent before preparation. Do not restore it from
`actions/cache`, download it from artifacts, or use the repository default
`.sage/legion-memory/...` path in GitHub Actions.

The existing `build_or_update_graph_tool` still owns the build. An empty path
naturally selects its full-build behavior, so no second `build_fresh_graph`
entry point is needed. Record `build_type=full`, `indexed_sha`, repository ID,
parser version, counts, and duration in `legion-memory.json`.

Do not explicitly upload or delete the graph in the normal workflow. The hosted
runner lifecycle discards it. Tests may clean their own temporary directories.

### 4.2 Qdrant: durable, repository-scoped vector storage

Remote Qdrant must not depend on `memory_namespace` from the fresh SQLite file.
Use a deterministic, secret-free collection scope derived from:

- normalized repository identity;
- embedding provider/model/dimension/endpoint identity; and
- document/query recipe and normalization versions.

Hash the scope before using it in a collection name. Store the full expected
non-secret fingerprints in payloads and validate them on reads. A repository
rename or embedding identity change may create a new isolated scope; it must
never read another repository or model's vectors.

The recommended shared-remote representation uses one deterministic collection
per repository and embedding identity with two immutable point record types:

1. **content cache points** keyed by qualified symbol identity plus exact node
   text hash; and
2. **snapshot points** keyed by graph generation plus qualified symbol identity,
   with payload filters for that exact published generation.

The SQLite manifest remains the authority for which Qdrant generation is valid
for the current graph, but it is no longer the only way to discover reusable
vectors. On a fresh graph build:

1. compute the deterministic graph generation from accepted SHA, parser version,
   embedding fingerprint, and the sorted eligible node/text-hash manifest;
2. check whether all expected snapshot points already exist and validate their
   identity, text hashes, vector dimensions, and finite values;
3. otherwise fetch matching content-cache points directly from Qdrant;
4. call Gemini only for missing or invalid content hashes;
5. idempotently upsert and verify cache points, then snapshot points;
6. publish the ready generation to the fresh SQLite graph in one short
   transaction only after the graph SHA still matches; and
7. query only snapshot points carrying that exact published generation.

This preserves zero-document-call reuse for an identical rebuild and selective
reuse across SHAs when node text is unchanged. Qdrant stores vectors and bounded
identity metadata, not graph edges, source bodies, Issue text, prompts, agent
transcripts, run state, or credentials.

If a simpler representation is implemented, it must still pass the same fresh-
SQLite reuse, generation isolation, crash-safety, and concurrency tests. Merely
making the current random namespace deterministic is insufficient because a
fresh SQLite `vector_nodes` table still cannot discover prior content reuse.

### 4.3 Shared-server concurrency and cleanup

Use immutable, deterministic point IDs and idempotent upserts so same-generation
and different-generation writers do not need a cross-run SQLite lock. Preserve
the existing local file lock for one SQLite graph, but do not mistake it for a
distributed Qdrant lease.

Replace request-path `prune(keep_generation=...)` behavior for shared remote
Qdrant. Search must filter the exact generation, so older snapshot points are
harmless. Cleanup may delete only points older than a documented retention
window that exceeds the 90-minute solve job limit, after the current run has
refreshed the points it uses. Cache retention must be longer than snapshot
retention so ordinary source changes still reuse unchanged node vectors.

Cleanup must be bounded, repository/model scoped, idempotent, and non-fatal once
the current generation is published. If safe retention cannot be completed in
the first implementation, leave old points in place, report cleanup as pending,
set an explicit collection/quota alert, and deliver deletion as a separate
maintenance change before broad rollout. Never delete a collection from a solve
request.

### 4.4 Failure behavior

Apply these distinct policies:

| Failure | GitHub behavior |
| --- | --- |
| Embeddings explicitly disabled | Build fresh graph, perform lexical retrieval, bind memory tools, make no embedding or Qdrant calls. |
| Enabled but URL/API key missing, local path configured, URL insecure, or settings invalid | Fail configuration before the first model or embedding call; do not silently use local Qdrant. |
| Tree-sitter/SQLite graph build unavailable | Preserve the existing visible memory fallback and continue the normal solve without memory. |
| Gemini or Qdrant runtime failure after valid configuration | Preserve the ready graph, record vectors unavailable and the safe category, continue lexical memory retrieval. |
| Qdrant data fails identity/dimension/generation validation | Ignore the invalid points, repair within budgets when possible, otherwise use lexical memory and record the failure. |
| Graph or retrieval SHA differs from the accepted base | Reject that memory session; never expose stale memory. |

The canary release gate is stricter than runtime fallback: it must show a full
graph build and ready vectors. A lexical fallback can keep an individual solve
useful, but it does not prove this integration is ready to release.

---

## 5. Configuration contract

GitHub Actions enables memory unconditionally for accepted solve commands and
enables embeddings by default. Local `sage solve` and `make solve` behavior stays
unchanged; local callers still opt into memory with `--memory-file` or
`make legion-solve`.

Add these composite-action inputs and map them only into the solve controller
step:

| Action input | Environment variable | Installed workflow source | Default/requirement |
| --- | --- | --- | --- |
| `legion-embeddings-enabled` | `SAGE_LEGION_EMBEDDINGS_ENABLED` | `${{ secrets.SAGE_LEGION_EMBEDDINGS_ENABLED || 'true' }}` | `true`; exact false-like values disable embeddings only |
| `legion-qdrant-url` | `SAGE_LEGION_QDRANT_URL` | `${{ secrets.SAGE_LEGION_QDRANT_URL }}` | Required and HTTPS when embeddings are enabled |
| `legion-qdrant-api-key` | `SAGE_LEGION_QDRANT_API_KEY` | `${{ secrets.SAGE_LEGION_QDRANT_API_KEY }}` | Required when embeddings are enabled |

Explicitly set `SAGE_LEGION_QDRANT_PATH` to an empty value in the GitHub solve
step. This prevents an inherited value from selecting local mode. Reuse the
existing `GEMINI_API_KEY`, embedding model/dimension/budget settings, and Google
context acknowledgement. Optional non-secret embedding tuning remains a
repository variable only when there is a demonstrated need; do not duplicate
all local settings as action inputs preemptively.

Configuration precedence for GitHub is:

```text
explicit workflow input from repository/environment secret
  -> composite input default (embeddings=true only)
  -> typed config validation
```

`false` must not be lost through a truthiness fallback. Tests must cover an
unset secret, the literal string `false`, accepted true/false aliases, and an
invalid value. No secret value or Qdrant endpoint may appear in CLI output,
logs, Issue status comments, traces, or artifacts.

The install guide must tell maintainers to create these repository or GitHub
environment secrets before enabling the pinned workflow:

```text
SAGE_LEGION_QDRANT_URL
SAGE_LEGION_QDRANT_API_KEY
SAGE_LEGION_EMBEDDINGS_ENABLED   # optional; set false to disable vectors
```

---

## 6. Implementation phases

### Phase A — Make Qdrant independent of ephemeral SQLite

Owners: `domain/embeddings.py`, `legion_memory/vectors.py`,
`integrations/qdrant.py`, existing Legion tests.

1. Extend the provider-neutral vector-store contract with the minimum batch
   lookup/filter/upsert operations required for cache and exact-generation
   points. Keep Qdrant SDK types inside the adapter.
2. Define deterministic collection, cache-point, snapshot-point, and graph-
   generation identities as pure functions with direct tests.
3. Store and validate repository, embedding, record-type, generation,
   qualified-name, and text-hash payload fields.
4. Refactor synchronization to recover verified vectors from Qdrant when the
   SQLite manifest is new or empty.
5. Publish current vector readiness back into SQLite only after complete Qdrant
   acknowledgement and a graph-SHA recheck.
6. Change remote cleanup so it cannot delete another active generation.
7. Preserve existing local-mode commands and lexical fallback. Do not add a
   second vector implementation specifically for Actions.

Exit: two fresh SQLite databases for clean clones of the same repository/SHA
and shared fake Qdrant produce equivalent retrieval; the second build makes
zero document-embedding calls. A one-symbol change embeds only that changed
content. Concurrent different-generation tests cannot cross-query or prune.

### Phase B — Bind memory into the GitHub solve lifecycle

Owners: `workflows/github.py`, `cli.py`, `composition.py`, workflow tests.

1. Add one helper that derives and validates the run-attempt-specific SQLite
   path beneath `runner_temp` and outside every untrusted or uploaded path.
2. Set `SolveRequest.memory_file` for every accepted GitHub invocation.
3. Add an injected memory-service factory or equivalent narrow dependency so
   construction happens only after the repeat gate passes. Do not construct
   provider adapters for rejected, unauthorized, duplicate, or stale requests.
4. Load `LegionEmbeddingSettings` through `config.py`, build the service through
   `composition.py`, and pass it to the existing `solve_issue` call.
5. Preserve existing test injection for `solve_runner`; update its protocol in
   one place rather than branching production workflow logic for tests.
6. Confirm memory preparation occurs against `PreparedRun.workspace_dir` after
   sandbox startup and before the first model call, as in the local workflow.

Exit: an offline GitHub workflow test observes a non-null, fresh memory path,
the exact accepted SHA, one memory-service construction after authorization,
and one call through the existing solve workflow.

### Phase C — Wire the composite action and installed workflow

Owners: `.github/actions/sage-solve/action.yml`, `.github/workflows/sage.yml`,
action policy tests, GitHub doctor.

1. Add the three inputs in section 5 to the solve composite action.
2. Scope their environment variables to `Solve and publish the authorized
   Issue`; do not expose them to checkout, dependency installation, Docker
   build, gate, finalize, or diagnostic upload steps.
3. Pass Qdrant and embedding secrets from the installed workflow. Preserve the
   empty top-level permissions map and existing job permissions.
4. Keep the existing per-Issue concurrency behavior; Qdrant correctness must
   not rely on globally serializing all repository solves.
5. Extend `make actions-check` assertions for defaults, immutable action pins,
   exact secret placement, absence from non-solve jobs, and no local Qdrant
   path/cache step.
6. Extend `github-doctor` to validate that the workflow contains the required
   secret wiring and default-on expression without reading secret values.

Exit: action YAML parses, every external action remains pinned to a full SHA,
and policy tests prove secrets reach only the trusted solve controller.

### Phase D — Add safe memory evidence and user documentation

Owners: `integrations/github/diagnostics.py`, workflow upload allowlist,
`docs/architecture.md`, `docs/testing.md`, `.env.example`, tests.

1. Add `legion-memory.json` to both the controller copy allowlist and workflow
   upload allowlist.
2. Keep the artifact bounded and secret-safe. It may include repository-relative
   locators, graph/vector status, generation fingerprint, counts, durations,
   search modes, exposure flags, and usage counts. It must not include Qdrant
   URL/key, provider payloads, Issue body, prompt, source body, or raw tool
   arguments.
3. If the current artifact contains a field that violates this GitHub upload
   boundary, add a redacted serialization view or a separate bounded memory
   summary rather than weakening the allowlist.
4. Update architecture documentation to distinguish ephemeral Actions SQLite
   from persistent local SQLite and to describe shared remote generation
   isolation.
5. Update the user-friendly testing guide with secret setup, embeddings-off
   operation, expected diagnostic fields, Qdrant troubleshooting, offline
   checks, optional live-Qdrant integration, pinning, and the canary procedure.
6. Keep `.env.example` local defaults explicit. Do not change local embeddings
   to default-on merely because the GitHub action defaults on.

Exit: the diagnostic artifact proves requested/enabled/ready/fallback state
without exposing secrets or unbounded repository content.

### Phase E — Verify, publish, pin, and canary

This phase is an operational release sequence, not an ordinary test fixture.

1. Run focused deterministic tests while implementing:

   ```bash
   uv run --project apps/agent pytest -c apps/agent/pyproject.toml \
     apps/agent/tests/legion_memory/test_vectors.py \
     apps/agent/tests/workflows/test_github.py \
     apps/agent/tests/actions/test_actions.py
   ```

2. Run the repository gates:

   ```bash
   make check
   make graph
   make github-smoke
   make github-doctor
   make actions-check
   ```

3. Optionally run a disposable Docker Qdrant integration test with fake
   embeddings. Normal unit tests must not require Qdrant Cloud or paid Gemini
   calls.
4. Review and commit the implementation, direct tests, and current docs as one
   coherent signed-off implementation commit, or split vector persistence from
   GitHub wiring if each commit remains independently coherent.
5. Push the implementation commit so its full 40-character SHA is reachable in
   `24aysh/sage`. A target workflow cannot download an unpushed SHA.
6. In a separate signed-off pin/install commit, replace both Sage composite
   action references in `.github/workflows/sage.yml` and the corresponding test
   constant with that implementation SHA. Never pin `main`, a branch, a mutable
   tag, the pin-only commit itself, or a later documentation-only commit.
7. Re-run the action and doctor checks, push the pin commit, and confirm the
   target repository/default branch contains the pinned workflow plus the three
   configured secrets.
8. Create one bounded Issue in the authorized canary repository and add one
   exact `/sage solve` comment. Do not repeatedly comment while a run is active.
9. Inspect the status lifecycle, draft PR, checks, and allowlisted diagnostics.
10. Repeat only if needed with the same base SHA to prove Qdrant reuse, then set
    `SAGE_LEGION_EMBEDDINGS_ENABLED=false` for one lexical-only smoke if the
    change window permits. Restore the intended secret afterward.

The pinned SHA cannot be filled into this plan in advance: it is the immutable
SHA produced after implementation. The pin commit will necessarily be newer
than the implementation SHA it references; this is the repository's existing
self-hosted composite-action release pattern.

---

## 7. Test matrix

### 7.1 Configuration and composition

- GitHub default resolves embeddings to enabled when the optional toggle secret
  is absent.
- Literal `false` disables embeddings without disabling the graph.
- Invalid boolean input is rejected safely.
- Enabled GitHub mode requires HTTPS Qdrant URL and API key and rejects a local
  Qdrant path.
- Disabled mode does not initialize an embedding or Qdrant client and ignores
  unused Qdrant configuration.
- Rejected gate paths do not load model, embedding, or Qdrant configuration.
- No error, model representation, or artifact contains secret values or the
  configured Qdrant endpoint.

### 7.2 Fresh graph and persistent vectors

- Every run attempt receives a different empty SQLite path.
- Build provenance equals the gate's exact base SHA and build type is `full`.
- Same repository/SHA/model/recipe with a second new SQLite file reuses all
  document vectors from Qdrant.
- Changed node text embeds only the missing content; unchanged nodes reuse.
- Deleted/renamed symbols cannot appear in the current generation.
- Changed model, dimensions, endpoint identity, or recipe cannot reuse an
  incompatible vector.
- Foreign repository points, malformed payloads, wrong dimensions, missing
  points, partial upserts, and unpublished generations never enter search.
- Same-generation retries are idempotent after interruption.
- Concurrent different-SHA generations remain independently queryable.
- Cleanup cannot delete a generation inside the maximum active-run window.

### 7.3 GitHub workflow behavior

- `SolveRequest.memory_file` is set only after repeat authorization and lies
  below trusted runner temp.
- The existing exact checkout SHA and publication base checks remain unchanged.
- Embeddings-on prepares hybrid memory before the first Solver call.
- Embeddings-off prepares lexical memory and still exposes native memory tools.
- Graph failure continues without memory and records a safe fallback.
- Runtime vector failure continues with lexical memory and records vectors as
  unavailable.
- `legion-memory.json` is copied/uploaded only through the exact allowlist.
- Gate/finalize jobs and all non-solve action steps remain free of model and
  Qdrant credentials.
- No Actions cache or artifact path includes SQLite, Qdrant data, checkout,
  candidate workspace, or Issue body.

### 7.4 Canary evidence

For the release canary, require all of the following:

- gate status accepted the actor and froze the expected base SHA;
- solve status progressed once from working to the correct terminal state;
- `legion-memory.json.build.build_type` is `full`;
- graph `indexed_sha` equals GitHub `original_base_sha` and solve `base_sha`;
- vector status is `ready`, model/dimensions match configuration, and the
  Qdrant operation count is non-zero;
- retrieval reports a semantic or hybrid channel for an Issue intentionally
  written to match indexed code;
- exposure says memory context was exposed or a native memory tool was queried;
- the normal verified/reviewed candidate becomes a creation-only branch and
  draft PR; and
- uploaded diagnostics contain no secrets, Qdrant endpoint, Issue body, or
  unbounded source content.

A no-match result is valid runtime behavior but is not a suitable positive
integration canary. Choose a small Issue with a known symbol/path relationship.

---

## 8. Security and operational invariants

1. The gate remains model- and Qdrant-secret free.
2. The target repository and Issue are untrusted inputs; neither can select a
   Qdrant endpoint, collection, namespace, memory path, or embedding policy.
3. Qdrant and Gemini network calls occur only in the trusted controller. The
   Docker sandbox remains network-disabled.
4. Repository source sent for embeddings remains bounded to the existing
   versioned node-text recipe and existing ignored paths. Enabling embeddings
   is explicit authorization to send that bounded text to Google and Qdrant.
5. Qdrant payloads contain no credentials, raw Issue text, prompts, agent
   transcripts, or full source bodies.
6. Collection names and point IDs reveal only hashes, not repository names,
   secret endpoints, or credentials.
7. All provider and adapter errors remain normalized and bounded.
8. A Qdrant compromise must not let stored payload text become executable
   instructions. Returned identities are validated against the accepted-SHA
   SQLite graph, and memory stays inside the untrusted context envelope.
9. GitHub token permissions and publication safety are unchanged.
10. The default-on rollout must include Qdrant capacity, request latency,
    embedding spend, collection/point count, failure rate, and fallback-rate
    monitoring.

---

## 9. Compatibility, migration, and rollback

- No dependency should be added: `qdrant-client`, Tree-sitter packages, Gemini,
  Pydantic, and the existing action toolchain are already present.
- Preserve public local CLI flags and Make targets.
- Preserve existing local SQLite migration support. A vector schema or payload
  recipe change must increment its explicit identity; never reinterpret old
  points silently.
- Existing local Qdrant collections may remain readable during migration or be
  rebuilt through the documented local command. Do not bulk-delete them as part
  of GitHub rollout.
- Deploy Qdrant configuration before pinning the action. Missing configuration
  with default-on embeddings is an installation error, not permission to use a
  local runner directory.
- Operational rollback is an immutable workflow-pin change to the last known
  good Sage action SHA. Do not rewrite or force-move the implementation commit.
- The non-destructive feature fallback is the optional secret
  `SAGE_LEGION_EMBEDDINGS_ENABLED=false`, which retains fresh lexical memory and
  avoids Gemini embedding and Qdrant calls.
- If graph preparation itself causes an incident, roll back the workflow pin.
  Do not add an undocumented mutable kill switch during implementation.

---

## 10. Non-goals

This integration does not:

- persist `graph.sqlite3`, Actions caches, candidate workspaces, or run state;
- use Qdrant as the graph database, agent checkpoint, transcript memory, or
  source-of-truth store;
- add an MCP server, daemon, watcher, runtime selector, or alternate solve path;
- change Reviewer inputs or authority;
- make repository commands network-capable;
- allow Issue text or repository files to choose secrets or endpoints;
- guarantee semantic matches, token savings, cheaper solves, or better patches;
- auto-merge the resulting draft pull request; or
- commit, push, pin, configure secrets, or launch a canary merely because this
  plan file exists.

---

## 11. Completion checklist

- [x] Qdrant collection and reuse identity no longer depend on the random
      namespace of an ephemeral SQLite file.
- [x] Fresh-SQLite same-SHA tests prove zero document-embedding calls on the
      second build.
- [x] Shared-Qdrant generation and cleanup behavior is concurrency-safe.
- [x] GitHub creates a fresh graph at the exact accepted SHA for every solve.
- [x] GitHub memory is always requested and embeddings default to true.
- [x] The false toggle preserves lexical memory and makes no embedding/Qdrant
      call.
- [x] Enabled GitHub mode requires remote HTTPS Qdrant URL and API key.
- [x] Secrets exist only in the trusted solve controller step.
- [x] `legion-memory.json` is bounded, redacted where necessary, and uploaded.
- [x] Focused tests and all canonical checks pass.
- [x] `docs/architecture.md`, `docs/testing.md`, and `.env.example` describe the
      implemented behavior accurately.
- [x] The implementation changes are split into signed-off local commits.
- [ ] The implementation commits are pushed and reachable publicly.
- [ ] Both Sage action references are pinned to that exact full SHA.
- [ ] One controlled `/sage solve` canary creates a draft PR and satisfies the
      evidence requirements in section 7.4.
