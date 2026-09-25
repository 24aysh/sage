# Sage architecture

Sage turns an authorized Issue into a verified, independently reviewed draft
pull request. One OpenAI-backed Solver plans and edits; one Gemini-backed
Reviewer judges the candidate. Deterministic Python controls the lifecycle.
There is one solve architecture and no runtime selector.

## Read the system through one solve

```text
CLI / trusted GitHub Action
  -> workflow: accept Issue and base SHA, prepare clean checkout
  -> start isolated sandbox; optional verification-tooling preflight
  -> optional memory: index accepted HEAD, retrieve bounded navigation context
  -> orchestrator:
       Solver session -> Git candidate -> verification -> Reviewer
            ^                                               |
            +------------- bounded fresh-session repair ----+
  -> final base-SHA and diff-digest guard
  -> persist terminal evidence; close sandbox and memory session
  -> GitHub only: creation-only branch and draft PR
```

The outer controller is ordinary Python. LangGraph implements only the
bounded model/tool loop within a Solver session. A repair gets a fresh session
and a fresh review. Sage never merges or marks a draft ready.

Three different facts must stay distinct:

| Fact | Authority | Consumer |
| --- | --- | --- |
| What should change | Issue and persisted Solver plan | Solver, verifier, Reviewer |
| What actually changed | Git at the accepted base SHA | Candidate guard, artifacts, publication |
| Where to look | Current source reads and optional base-snapshot memory | Solver |

Memory cannot satisfy acceptance criteria, grant mutation authority, or replace
current source. Model summaries cannot define a diff or changed-file list.

## The harness: one evidence lifecycle

The harness lives in `apps/agent/src/sage/harness/`. It supports an agent's
working context without deciding whether the Issue is solved. Each boundary has
a single question to answer:

| Layer | Question | Owner |
| --- | --- | --- |
| Task and policy | What must this role accomplish and obey? | Issue, `agents/prompts.py`, `harness/context/instructions.py` |
| Context | What evidence belongs in this invocation? | `harness/context/packets.py`, `run.py`, `tools.py` |
| Memory | Where should I inspect, and why? | `harness/memory/` |
| Optional judgment | Which retrieved files belong in initial context? | `harness/jev/` |
| Current facts | What does the candidate actually contain? | `repository/` |
| Decision and proof | Is the candidate verified, reviewed, and safe to publish? | `orchestration/`, `verification/`, `artifacts/` |

The flow is one accepted base, immutable role guidance, bounded initial graph
context, current source reads, optional attributed enrichment, a saved plan,
edits with stale-location invalidation, verification, and independent review.
Every repair gets a fresh history with the same role guidance and base identity.
Evidence deduplication resets with that history; it must not suppress facts that
the new session has never seen. Within a history, previously exposed graph facts
are not repeatedly appended. Source reads retain priority when budgets run out.

`sage-solver.md` and `sage-reviewer.md` are discovered automatically in the
accepted checkout. `SAGE_SOLVER_INSTRUCTIONS_FILE` and
`SAGE_REVIEWER_INSTRUCTIONS_FILE` in `sage.yml` (or local `.env`) select relative
paths. Missing files mean no additional role guidance. Files are UTF-8, capped
at 12,000 bytes each, and cannot escape the repository. They are read once before
models start and kept in each role's system message for every invocation,
including repairs and rereviews. This costs input tokens on each request;
keep guidance concise. No implicit summarizer or extra model call is added.
Sage's role, Issue scope, plan gate, verification, safety, and output contracts
take precedence. Edits during a solve cannot rewrite its active guidance.

The harness is composed of focused capabilities, not a global state manager.
Its context object is frozen; mutable visibility and budgets belong to explicit
run-scoped sessions. `composition.py` constructs services and optional Jev clients.
`config.py` remains the environment boundary. Shared provider-neutral contracts
remain in `domain/`, so repositories and accounting need not import a concrete
Jev adapter. CLI and GitHub Issue collection stay with their transport owners.

## Jev file relevance filter

The only Jev pipeline is `lexical retrieval → file relevance filter → Solver
context`. The old tool-triggered excerpt/action navigation implementation is
removed. `harness/jev/filter.py` groups the deterministic shortlist by file;
`provider.py` batches one independent Score question per file over the same Issue.
No inference runs inside repository tools, and Jev cannot request reads, searches,
graph queries, edits, verification, review, or publication.

Memory still uses exact/FTS seeds and bounded graph expansion. With Jev enabled,
candidate metadata is retained within a 50,000-character staging budget (normally
at most 12 items), then accepted items are rendered using the existing final
context limit: 4,000 characters for solve, 12,000 for standalone retrieval.
These are metadata limits, not whole-file reads. File names, symbol signatures,
language, and selection reasons form bounded evidence for Jev. The complete Issue
must fit 6,000 characters; the complete JSON request must fit 16,000 bytes.
Oversized input is explicitly withheld, not silently truncated and approved.

`SAGE_JEV_NAVIGATION_MODE` retains its name for compatibility:

- `off` (default): deterministic lexical/graph context; no Jev call.
- `shadow`: one judgment, but context selection remains lexical; hypothetical
  discards are recorded separately.
- `on`: only files meeting both `SAGE_JEV_RELEVANCE_SCORE_THRESHOLD` (default 2
  on a 0–3 scale) and `SAGE_JEV_RELEVANCE_CONFIDENCE_THRESHOLD` (default 0.5) enter
  context. Keep lexical ordering among survivors. Confidence is distribution
  concentration, not a probability that the solve will succeed.

The old navigation policy, action thresholds, follow-up count, and run-wait
settings are retired; enabled modes reject them with migration guidance. Off
mode ignores retired settings so `solve-baseline` remains usable.

One request uses the existing two-second maximum timeout, no retries, strict
answer IDs/distributions/score validation, and actual API token accounting.
Empty/unavailable memory skips inference. Timeout, invalid response, oversized
input, and insufficient time withhold unjudged context in `on` mode, with a
visible reason; they are not reported as model rejections. All-rejected is a
valid empty selection. Ordinary source inspection remains available in both
cases. No new dependency or embedding provider is introduced.

The initial filter runs inside a measured Solver-context stage before the first
generative call. Semantic usage remains in `usage.json`; solver time includes
Jev, with its duration separately available for subtraction and Ctrl-C reporting.
Repair histories reuse accepted, unchanged locators without another Jev call.
Automatic read/search graph enrichment is disabled in `on` mode so it cannot
immediately reintroduce discarded memory. Explicit Solver-requested source and
graph tools remain capabilities, not a file-access allowlist.

`relevance-filter.json` records file decisions, thresholds, retained/rejected/
withheld counts, context-budget omissions, time, and tokens. Standalone retrieval
writes `graph.relevance.json` plus its normal context and retrieval files. Input
logging and exact captures are sensitive and independently configurable; GitHub
forces them off and exports only an allowlisted numeric/status summary. It also
sanitizes the nested filter report in memory diagnostics.

The benefits are fewer tool-loop API round trips and a smaller initial context;
neither guarantees an end-to-end improvement. Metadata-only judgments can reject
useful code or accept irrelevant symbols in an otherwise relevant file. Evaluate
fixed Issue/base/model pairs with independent outcome checks. The evaluator
supports `off`, `shadow`, and `on`; it refuses historical navigation artifacts
instead of comparing incompatible pipelines silently. See
[testing](testing.md#jev-relevance-filter) and the
[implementation plan](relevance-filter-plan.md).

## Find the owner before changing code

All production Python paths below are relative to `apps/agent/src/sage/`.
Tests mirror owners under `apps/agent/tests/`.

| Change or question | Start here | Boundary to preserve |
| --- | --- | --- |
| Command flags, dispatch, exit policy | `cli/app.py`, `cli/solve.py`, `cli/memory.py`, `cli/github.py` | CLI names and installed `sage.cli:main` entrypoint |
| Terminal output | `cli/output.py` | Stable output and redaction |
| Concrete dependency wiring | `composition.py` | Construct adapters explicitly |
| Environment/settings | `config.py`, `integrations/github/config.py` | Existing settings loaders; no environment reads in capabilities |
| Resource startup/cleanup | `workflows/solve.py`, `workflows/github.py` | Cleanup on success, failure, and cancellation |
| Solve/repair decisions | `orchestration/solve.py`, `validation.py` | Bounded routing independent of provider wire formats |
| Candidate truth | `orchestration/candidate.py` | Git-derived paths/diff and final digest guard |
| Solver role and mutation gate | `agents/solver.py`, `prompts.py` | Persist implementable plan before mutation |
| Role loop execution | `agents/loop.py` | Typed, bounded model/tool loop |
| Context and tool delivery | `harness/context/`, `harness/memory/tools.py` | One evidence path, immutable instructions, bounded output |
| Jev relevance filter | `harness/jev/filter.py`, `provider.py`, `domain/relevance.py` | One batched file judgment before context; no tool execution |
| Reviewer packet and contract | `agents/reviewer.py`, `domain/review.py` | Independent judgment and complete criterion coverage |
| File, search, Git, command operations | `repository/` | Validated paths, command allowlist, bounded output |
| Verification | `verification/` | Deterministic checks; preflight is tooling readiness, not test evidence |
| Models and call accounting | `providers/` | Bounded retries, typed outputs and usage |
| Run evidence | `artifacts/store.py`, `artifacts/files.py` | Atomic run-scoped writes |
| GitHub transport/publication | `integrations/github/` | Authorization, credential isolation, creation-only publication |
| Sandbox | `sandbox/` | Disposable, resource-bounded, network-disabled execution |
| Web presentation | `apps/web/DESIGN.md` from repository root | Independent of backend solve control |

`domain/` holds typed contracts; `harness/context/run.py` carries run-scoped
dependencies. Package names describe responsibilities. A generic helper or
another runtime layer is not needed to connect them.

## Local graph memory

`LegionMemoryService` remains the public repository-bound capability. Its
implementation delegates committed-source indexing to `RepositoryIndex`,
using the existing parser and SQLite store. Memory requires no model credentials
and performs no network calls.

| Responsibility | Owner in `harness/memory/` |
| --- | --- |
| Git root, identity, source inventory, full/incremental/no-change build | `indexing.py` |
| Grammar extraction and symbol metadata | `parsing.py`, `symbol_metadata.py` |
| Scoped imports, aliases, typed receivers | `resolution.py`, `bindings.py`, `tsconfig.py` |
| SQLite transactions, migrations, graph reconciliation | `store.py`, `migrations.py` |
| Communities and structural diagnostics | `communities.py`, `analysis.py` |
| Validated read operations and result provenance | `service.py`, `queries.py`, `review.py` |
| Issue signals, lexical ranking, expansion, bounded packet | `retrieval.py` |
| Accepted-base preparation and build serialization | `preparation.py`, `locking.py` |
| Run/session visibility, deduplication and edit invalidation | `session.py`, `context.py` |

`bindings.py` infers dependency bindings from parsed source. It never constructs
Sage services; production construction belongs to `sage/composition.py`.

### Committed source and storage

Indexing reads blobs from committed `HEAD`, not dirty files. The requested
repository must equal its resolved Git root. SQLite records repository identity,
exact SHA, schema/parser versions and readiness. One operation selects full,
incremental, or no-change processing. Full and incremental results use the same
result assembly. Directory exclusions reuse `repository/selection.py`, with
memory-specific exclusions added explicitly.

Tree-sitter supplies grammar extraction, NetworkX graph analysis, and igraph
seeded weighted Leiden communities. The supported language table lives in
`parsing.py`; HTML and CSS contribute stable-id elements, class/id selectors,
local stylesheet/script imports, and selector references. Anonymous HTML tags
and remote resources are omitted to bound graph size and noise. Stored
relationships include containment, imports, calls, inheritance, tests,
references, routes, events and configuration keys. Updates
reconcile aliases and shared identities after deletion. Ambiguous or dynamic
dispatch remains unresolved. Indexed JSONC/relative single-parent tsconfig
inheritance is bounded; package-based or multiple-parent inheritance is unsupported.

### Retrieval and exposure

Local `make solve` is memory-free. Explicit `legion-solve` requests memory.
Accepted GitHub solves always build a fresh SQLite graph under runner temporary
storage; that database is not cached or uploaded.

The workflow builds after sandbox startup and optional tooling preflight, before
model calls. Retrieval uses bounded Issue paths, identifiers and terms, exact/FTS
ranking, and bounded relationship/flow/community expansion. Exact identifiers
and explicit paths anchor the ranking; results include reasons and source
locations. There is no vector index, semantic seed retrieval, or embedding API.

| Retrieval result | Solver exposure |
| --- | --- |
| `used` | Untrusted context packet and five solve-profile tools |
| `no_match` | No initial packet, graph tools, or enrichment queries |
| `unavailable` | Normal source inspection with an explicit fallback artifact |

The five solve tools are lexical `search_nodes_tool`, query patterns, flow, community, and
impact. The full native registry retains 21 operations for explicit use. All are
read-only, accept neither arbitrary SQL nor a database path from the model, and
retain bounded schemas/defaults/order. The former `semantic_search_nodes_tool`
is renamed to `search_nodes_tool` to accurately describe its lexical behavior. The Reviewer receives candidate evidence
without memory tools.

Initial solve context defaults to 4,000 characters. In Jev `on` mode, only filtered
items reach this packet and automatic read/search enrichment is disabled.
Otherwise read/search enrichment is
lexical and makes no hidden embedding calls. It respects source ranges, tool
output limits, and 3,000-character per-call, 16,000-character history and
48,000-character run caps. Session resets restore base locators; edits suppress
stale enrichment and remove old line ranges from native responses. Duplicate
visible responses are suppressed. Source output survives enrichment failures.

### Storage migration and concurrency

Schema 4 removes obsolete `vector_nodes`, `vectors:*` metadata and the unused
memory namespace. The ordered schema history remains solely to upgrade existing
graphs safely. Graph rows, provenance, source bindings, and FTS remain intact.
Builds apply migrations before deciding full/incremental/no-change processing;
read-only queries require a current schema and ask for a build when outdated.
Writers retain a nonblocking per-database file lock; SQLite WAL supports ready
graph readers. Standalone build/status/retrieve need only local Git and SQLite.

Embedding adapters, Qdrant integration, hybrid ranking, usage fields, settings,
CLI flags, Make overrides and secrets are removed. Existing external collections
and local Qdrant directories are not opened or deleted. Unused environment
variables cannot enable embeddings. `--embeddings` is no longer accepted.
Google's SDK remains transitively installed for the independent Reviewer.

## Dependency rules

Interfaces call workflows. Workflows own resource lifetimes and use the
orchestrator/capabilities. Agents and orchestration depend on typed capabilities;
external adapters implement those boundaries. Domain contracts form the stable
foundation. This is a directed dependency graph, not a requirement that every
operation pass through every layer.

Solver branch navigation follows the same boundary: `repository/git.py` owns
validated Git operations, `repository/service.py` owns the repository façade,
and `harness/context/tools.py` exposes thin `list_branches` and
`switch_branch` adapters. Switching requires a clean sandbox worktree. It does
not relax the candidate guard: an implemented result must still be based on the
accepted commit.

Executable guards in `tests/test_architecture.py` enforce:

- Domain imports only standard-library, Pydantic, and domain contracts.
- Agents/orchestration never reach into CLI, workflows, GitHub or concrete Docker.
- Providers and deterministic capabilities never reach back into agent control.
- The harness never imports agents, orchestration, CLI, workflows, GitHub or Docker.
- Memory never imports providers, Jev, or model configuration; retrieval is deterministic.
- Internal module imports are acyclic, including imports through package names.
- Initializers contain no implementation; the CLI only re-exports `main` for
  entrypoint compatibility.
- Source size and dependency counts stay bounded; command and indexing extraction
  does not justify unrelated framework growth.

## Evidence and failure diagnosis

One `RunArtifacts` instance owns atomic evidence for one run:

| Evidence | Question it answers |
| --- | --- |
| `request.json`, `metadata.json`, `issue.md` | What was accepted, and at which base? |
| `solver-plan.json`, `solver-final.json` | What did the Solver intend and report? |
| `candidate-snapshot.json`, `changed-files.json`, `diff.patch` | What did Git observe? |
| `verification-summary.json`, `review.json` | Which checks and criteria passed? |
| `usage.json` | Which model/tool calls and accepted commands consumed resources? |
| `legion-memory.json` when requested | Was memory available, retrieved, exposed, queried or enriched? |
| `terminal.json`, `agent-final.json` | Why did the run stop? |

Immutable histories live under `solver-plans/`, `verification/`, and `reviews/`.
Persisted format identifiers, trace labels, model environment names
(`SOLVER_MODEL`, `REVIEWER_MODEL`) and `sage-sandbox:v2` remain stable.

Repository failures and invalid tool arguments provide bounded correction
feedback. Provider failures, repair limits and candidate guard failures have
explicit terminal outcomes. Publication requires completed, nonempty, verified,
reviewed evidence at the accepted base. Host credentials never enter prompts,
candidate sandbox or uploaded diagnostics. GitHub uploads use a fixed allowlist
and remove rendered memory context.

## Make improvements accumulate

For a new capability, locate its deterministic owner, extend its typed contract,
expose a thin agent adapter if needed, and wire it in composition. For a new
decision, change the orchestrator and its routing tests. For new evidence, extend
the artifact contract and its producer/consumer together.

Keep the regression demonstrating a failure beside its owner. Share repeated
fixture setup, not unrelated assertions. Test names describe behavior rather than
implementation phases. Update this map and the [testing guide](testing.md) in the
same change. Preserve numbered specifications as historical rationale; the
[refactor plan](refactor-plan.md) records current simplification and verification.
