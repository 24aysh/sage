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

Phase 1 provides a local, rebuildable repository knowledge graph named Legion
Memory. It indexes source blobs from the selected repository's committed
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

The Phase 1 LangChain adapters are native, read-only, repository-bound tools;
they accept neither a database path nor arbitrary SQL from the model. They are
implemented and tested but are not yet bound to the Solver. Issue-specific
retrieval is Phase 2 and local solve integration is Phase 3. No MCP server,
daemon, watcher, graph mutation tool, model call, or network service is part of
the graph engine.

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
terminal.json
agent-final.json
changed-files.json
diff.patch
```

Immutable histories live under `solver-plans/`, `verification/`, and
`reviews/`. `changed-files.json` and `diff.patch` are authoritative. Persisted
metadata and trace labels retain their current format identifiers so existing
run consumers and operational trace history remain compatible.

## Security boundaries

- The source checkout is never the candidate workspace.
- Repository commands run only in a disposable, network-disabled Docker
  sandbox with bounded resources and timeouts.
- Host Git is limited to workspace preparation and the trusted publication
  transaction.
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

Measured on 4 September 2026 after Legion Memory Phase 1:

| Metric | Before | Current |
| --- | ---: | ---: |
| Production Python files | 81 | 82 |
| Production Python lines | 10,922 | 13,702 |
| Nonblank production Python lines | 9,383 | 11,922 |
| Solve coordinator | 631 lines | 324 lines |
| Highest internal module fan-out | 21 | 14 |
| Supported solve architectures | 1 behind selectors/factories | 1 constructed directly |

The current increase is the explicit Phase 1 parser, transactional SQLite
store, graph algorithms, native tool boundary, and typed contracts. Existing
solve orchestration remains unchanged. Compressing these boundaries would make
the system smaller but harder to audit. File count, coordinator size, and
dependency fan-out carry the intended navigation improvement, and the current
nonblank size is guarded against regression.

The detailed migration rationale and compatibility decisions are linked from
[`refactor-plan.md`](refactor-plan.md). Verification commands are in
[`testing.md`](testing.md).
