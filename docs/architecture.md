# Sage architecture

## What Sage is

Sage is a production-oriented issue-to-draft-PR system. It gives one
OpenAI-backed Solver a bounded set of repository tools, verifies the resulting
Git candidate deterministically, and asks an independent Gemini-backed
Reviewer to judge the actual diff. Sage may repair and re-review a candidate,
but it never merges or marks a draft pull request ready.

There is one supported solve architecture. It is constructed directly; there
is no runtime selector, version factory, or dormant implementation path.

## End-to-end sequence

```text
CLI or GitHub Action
  -> use-case workflow
    -> clean checkout at the accepted base SHA
    -> optional Legion Memory build/update and Issue retrieval
    -> network-disabled Docker sandbox
    -> deterministic SolveOrchestrator
      -> fresh Solver tool session
        -> inspect repository
        -> persist a complete plan
        -> mutate through structured file tools
      -> derive candidate from Git
      -> run deterministic verification
      -> independent read-only Reviewer
      -> completed result, bounded repair, or terminal outcome
    -> persist authoritative artifacts
    -> optionally publish a creation-only branch and draft PR
```

The outer loop is normal Python, not an agent graph. LangGraph is used only for
one bounded Solver tool session. Every repair starts a fresh session, and every
repaired candidate receives a fresh review.

## Legion Memory

Phases 1 through 3 provide a local, rebuildable repository knowledge graph named
Legion Memory plus deterministic Issue-relevant retrieval. The graph indexes
source blobs from the selected repository's committed
`HEAD`, stores repository-relative symbols and relationships in SQLite, and
records the repository identity, exact Git SHA, parser version, schema version,
and build state. One `build_or_update_graph_tool` operation chooses a full,
incremental, or no-change build; callers do not implement separate cold/warm
paths.

The graph includes FTS5 search, containment, imports, calls, inheritance,
test links, bounded flows, communities, impact traversal, hubs, bridges, and
knowledge-gap summaries. Tree-sitter provides grammar parsing and NetworkX
provides deterministic graph analysis. Supported grammars are Python,
JavaScript, TypeScript/TSX, Go, Rust, Java, C#, Ruby, C/C++/Objective-C,
Kotlin, Swift, PHP, Scala, Dart, Lua, Bash, Elixir, Zig, Julia, HCL, SQL,
YAML, Nix, PowerShell, Svelte, Vue, R, Perl, and Solidity.

The LangChain adapters are native, read-only, repository-bound tools;
they accept neither a database path nor arbitrary SQL from the model. A local
memory solve builds or updates the graph against the clean workspace before
the sandbox or first model call, retrieves bounded Issue context, and creates
a run-scoped memory session. Phase 2 retrieval
extracts bounded Issue paths, identifiers, error tokens, and terms, ranks exact
and FTS5 hits, expands the best seeds through relationships, flows, and
communities, and returns explainable source locators under result and character
budgets. A stale, foreign, missing, corrupt, or incompatible graph returns an
explicit unavailable result so the normal Solver can continue without memory.
A valid session binds all 15 native read-only tools to the Solver. Useful
initial results are included in an `<untrusted-legion-memory>` envelope;
`no_match` keeps the tools available for exploration without adding an empty
prompt section. Unavailable memory binds no graph tools. The Reviewer remains
unchanged and judges only the actual candidate evidence. No MCP server,
daemon, watcher, model call, or network service is part of retrieval.

The graph remains a snapshot of the accepted base SHA throughout mutations
and repairs. Solver instructions require current repository reads before
planning or editing. Every native memory call records only its name, status,
hit count, returned repository-relative paths, duration, and truncation state.
The provider call ledger separately records every model-requested tool name so
baseline and memory-assisted runs can compare tool and token totals without
persisting tool arguments.

The 15 functions in `agents/memory_tools.py` are the deliberately frozen
model-callable read-only subset from the Phase 1 specification, not a claim of
parity with all 30 tools exported by `code-review-graph`. Legion also owns the
pre-run build operation natively, for 16 implemented upstream-equivalent
capabilities in total. The omitted upstream set includes embedding, explicit
post-processing, source-mutating refactors, wiki writes, and multi-repository
registry operations, as well as read-only review helpers. Adding exact parity
requires a separate safety and ownership pass; write/build/maintenance tools
must not be exposed to the Solver merely to make the counts equal.

## Dependency tower

Dependencies point toward stable contracts:

```text
interfaces (CLI, Actions)
  -> workflows
    -> composition and orchestration
      -> agents
        -> deterministic capabilities
          -> providers/adapters
            -> typed domain contracts
```

The enforced rules are:

- `domain` imports only standard-library, Pydantic, and other domain modules;
- agents do not import CLI, workflows, GitHub integration, orchestration, or
  Docker implementations;
- orchestration does not import CLI, workflows, GitHub integration, or Docker;
- provider adapters do not import agents, orchestration, workflows, repository,
  research, verification, or sandbox packages;
- `legion_memory` does not import agents, CLI, composition, orchestration,
  workflows, integrations, providers, or sandbox packages;
- package `__init__.py` files document ownership and contain no implementation;
- non-composition modules have at most 14 internal module dependencies; and
- removed architecture and state-engine packages cannot reappear unnoticed.

These rules are executable in
[`test_architecture.py`](../apps/agent/tests/test_architecture.py).

## Ownership map

| Responsibility | Canonical owner |
| --- | --- |
| CLI and exit policy | `sage/cli.py` |
| Production construction | `sage/composition.py` |
| Environment loading | `sage/config.py` |
| Solver role, tools, plan gate | `sage/agents/solver.py` |
| Reviewer packet and contract | `sage/agents/reviewer.py` |
| Bounded model tool loop | `sage/agents/loop.py` |
| Solve/verify/review/repair routing | `sage/orchestration/solve.py` |
| Candidate derivation and final guard | `sage/orchestration/candidate.py` |
| Run-scoped dependency contract | `sage/orchestration/context.py` |
| Cross-role validation and outcome policy | `sage/orchestration/validation.py` |
| Repository capabilities | `sage/repository/service.py` |
| Safe file operations | `sage/repository/filesystem.py` |
| Deterministic verification | `sage/verification/` |
| Research budgets and cache | `sage/research/service.py` |
| Legion Memory build and query boundary | `sage/legion_memory/service.py` |
| Legion Memory parsing and SQLite graph | `sage/legion_memory/parsing.py`, `store.py` |
| Legion Memory Issue ranking and graph expansion | `sage/legion_memory/retrieval.py` |
| Run-scoped Legion Memory binding and usage evidence | `sage/legion_memory/session.py` |
| Native read-only memory adapters | `sage/agents/memory_tools.py` |
| Model adapters and call accounting | `sage/providers/` |
| Atomic run evidence | `sage/artifacts/store.py` |
| Local solve resource lifecycle | `sage/workflows/solve.py` |
| GitHub lifecycle | `sage/workflows/github.py` |
| GitHub API and transport | `sage/integrations/github/client.py`, `transport.py` |
| GitHub publication policy and Git transaction | `sage/integrations/github/publication.py`, `git_publish.py` |

Tests mirror these responsibility names under `apps/agent/tests/`.

## Agent boundaries

### Solver

The Solver receives the Issue and accepted base SHA. It can list the tree,
search exact text, read bounded file ranges, request bounded research, persist
or revise a typed plan, edit files through structured operations, show the
actual Git diff, and run allowlisted verification commands.

For an explicit memory-assisted local run, the Solver also receives bounded
untrusted base-snapshot locators and read-only graph tools. It must verify
those locators with current repository reads. A graph result cannot unlock
mutation or satisfy an acceptance criterion.

Mutation is locked until the Solver persists an implementable plan. The Solver
cannot use a raw patch tool, arbitrary mutation shell, Git commit or push,
credentials, or direct network access.

### Reviewer

The Reviewer receives the complete Issue, latest plan, Git-derived changed
paths and bounded diff, deterministic verification evidence, Solver summary,
and safe research provenance. It has no repository or mutation capability.
Passing review must cover every acceptance criterion exactly once and mark it
satisfied.

### Orchestrator

`SolveOrchestrator` owns model-call order, candidate snapshots, verification,
review routing, repair progress fingerprints, final candidate guards, and
terminal outcomes. It does not define role prompts, repository mechanics,
provider wire formats, or GitHub publication.

## Candidate and artifact truth

The candidate is always derived from Git at the accepted base SHA. Model claims
never define changed files or patch content. After a passing review, Sage checks
the base SHA and diff digest again before returning `completed`.

One `RunArtifacts` instance owns all evidence for a run. Writes are atomic.
The stable top-level set is:

```text
request.json
metadata.json
issue.md
solver-plan.json
solver-final.json
candidate-snapshot.json
verification-summary.json
review.json
usage.json
legion-memory.json              # memory-assisted local runs only
terminal.json
agent-final.json
changed-files.json
diff.patch
```

Immutable histories live under `solver-plans/`, `verification/`, and
`reviews/`. `changed-files.json` and `diff.patch` are authoritative. Persisted
metadata and trace labels retain their current format identifiers so existing
run consumers and operational trace history remain compatible.

`usage.json` includes provider-reported input, output, and cached tokens plus
the names of model-requested tools. `legion-memory.json` records build,
retrieval, fallback, and native memory-tool summaries without raw tool
arguments or source bodies.

## Security boundaries

- The source checkout is never the candidate workspace.
- Repository commands run only in a disposable, network-disabled Docker
  sandbox with bounded resources and timeouts.
- Host Git is limited to workspace preparation and the trusted publication
  transaction.
- Legion Memory is read-only to the model, repository-bound, SHA-validated,
  and always treated as untrusted navigation evidence.
- GitHub credentials are scoped to the controller and temporary publication
  environment; they never enter prompts, artifacts, the target sandbox, or Git
  command arguments.
- GitHub HTTP has bounded responses, pagination, retry delay, and normalized
  errors.
- Publication requires a completed, non-empty, verified, reviewed candidate at
  the exact accepted base.
- Branch push is creation-only, names remain `sage/issue-<number>`, pull
  requests are drafts, and duplicate/orphan states are reconciled safely.
- Diagnostic upload is an explicit file allowlist and never includes the
  checkout or Issue body.

## Configuration

`Settings.from_env` is the only environment-loading boundary. The two public
model settings remain `SAGE_V2_SOLVER_MODEL` and `SAGE_V2_REVIEWER_MODEL` for
operator compatibility. They map directly to the unversioned Python fields
`solver_model` and `reviewer_model`.

The canonical sandbox image is `sage-sandbox:v2`. The canonical LangSmith
project may remain `sage-v2` so trace history stays continuous. There is no
environment setting that selects another solve implementation.

## Extending the system

To add a Solver tool:

1. put deterministic behavior in the owning repository, research, or
   verification service;
2. expose a narrow typed adapter from `agents/solver.py`;
3. preserve the plan-before-mutation gate for mutation tools; and
4. add direct service tests plus agent capability tests.

To add an agent role:

1. create one role module with its packet, instructions, capability set, and
   typed output boundary;
2. construct it explicitly in `composition.py`;
3. add an explicit transition in the orchestrator;
4. add role-contract and routing tests; and
5. update this document and the testing guide.

Do not add a generic registry, provider abstraction, or package directory until
at least two real implementations need that boundary.

## Refactor measurements

Measured on 4 September 2026 after Legion Memory Phase 3:

| Metric | Before | Current |
| --- | ---: | ---: |
| Production Python files | 81 | 84 |
| Production Python lines | 10,922 | 15,105 |
| Nonblank production Python lines | 9,383 | 13,180 |
| Solve coordinator | 631 lines | 327 lines |
| Highest internal module fan-out | 21 | 14 |
| Supported solve architectures | 1 behind selectors/factories | 1 constructed directly |

The current increase includes the parser, transactional SQLite store, graph
algorithms, deterministic retrieval, native tool boundary, run-scoped memory
session, usage evidence, and typed contracts. Phase 3 extends the existing
solve lifecycle without adding another orchestration architecture. Compressing
these boundaries would make the system smaller but harder to audit. File
count, coordinator size, and dependency fan-out carry the intended navigation
improvement, and the current nonblank size is guarded against regression.

The detailed migration rationale and compatibility decisions are linked from
[`refactor-plan.md`](refactor-plan.md). Verification commands are in
[`testing.md`](testing.md).
