> Follow-up: [Jev relevance filtering](relevance-filter-plan.md) replaces the
> tool-triggered Jev behavior preserved by this earlier harness refactor.

# Agent harness refactor

## Current objective and design

Branch: `refactor/agent-harness`. The user requested one discoverable home for
context management, Legion Memory, and Jev, and complete removal of embeddings.
The unit of design is still one solve: task, role policy, source observations,
bounded context, candidate, verification, review, and terminal evidence.

The resulting tower of capabilities is explicit:

1. Domain contracts name facts without importing implementations.
2. Repository capabilities supply current source; graph memory supplies
   source-linked, accepted-base navigation with lexical ranking and reasons.
3. Harness context controls instruction lifetime, packet assembly, and delivery
   through read tools. Visibility and invalidation belong to a specific history.
4. Jev optionally selects complete read-only observations within existing budgets.
5. Thin roles reason with those capabilities. Deterministic orchestration owns
   saved-plan, candidate, verification, review, repair and publication gates.
6. Artifacts and tests record evidence and make subsequent improvements measurable.

An agent can inspect an Issue, follow an attributed source locator, see why it
was suggested, and recover using ordinary reads when optional context is absent.
It need not reason about embedding models, index publication, vector health,
remote stores, or duplicated retrieval modes. This removes resource costs and
failure states without replacing them with another inference step.

## Implementation checklist

- [x] Create `harness/context`, `harness/memory`, and `harness/jev` owners.
- [x] Move context envelopes and tool delivery out of role definitions; move
  memory preparation out of workflow resource management.
- [x] Load role markdown once from the accepted checkout and retain each role's
  guidance on every invocation, including repairs and rereviews.
- [x] Relocate Jev without changing payloads, policies, budgets, or accounting.
- [x] Remove embedding adapters, hybrid ranking, Qdrant dependency/configuration,
  vector artifact fields, CLI switches, and GitHub secret wiring.
- [x] Add schema 4 migration removing only obsolete vector state; preserve
  graph rows, source metadata, lexical ranking, and build locking.
- [x] Move tests to harness owners and add invariants for local-only memory,
  context lifetime, migration preservation, and dependency direction.
- [x] Complete canonical checks and record final results below.

## Tradeoffs and compatibility

The namespace is a discoverability boundary, not a new global manager. Sessions
keep separate, explicit lifetimes so a repair receives necessary facts again.
No persistent inferred beliefs, automatic summarizer, prompt-cache service, or
new learning subsystem is introduced. Improvements accumulate as regression
fixtures and versioned policy changes, checked against recorded tokens, time,
exposure, and independently verified outcomes.

Lexical retrieval cannot find every synonym-only match that embeddings could.
Exact paths, identifiers, FTS, graph relationships, and current source inspection
remain. `semantic_search_nodes_tool` becomes `search_nodes_tool`; its schema and
lexical behavior are retained. Jev remains optional with its existing defaults.

CLI `--embeddings` and Make embedding overrides are removed. Old environment
variables have no consumer. SQLite graphs upgrade on build. External Qdrant data
is untouched and must be managed by its owner; no remote cleanup is attempted.
The old migration SQL is retained to support existing graph databases.

Role markdown is bounded, role-specific, and read once, but appears in every
model request. Stable prompts may benefit from provider caching; the refactor
does not promise token savings from provider behavior. Keep guidance concise.

Existing Action pins remain at the prior release until an implementation commit
is published and deliberately pinned. Offline checks cannot certify deployment
or live quality/cost improvements. No commit, push, or paid call is implicit.

## Verification

Completed on `refactor/agent-harness`:

- `make check`: 567 passed, one optional reference-checkout test skipped;
  package compilation passed.
- `make graph`: passed.
- `make github-smoke`: passed using local substitutes, without model calls or
  remote publication.
- `make github-doctor`: passed with read-only Docker socket access.
- AST comparisons confirm Jev's provider, session, and candidates are unchanged
  apart from import paths; `JevSettings` is unchanged.
- Regression tests cover graph-preserving migration, local-only memory, writer
  locking, role-instruction snapshots across repairs/rereviews, context budgets,
  and harness dependency boundaries.
- `git diff --check`: passed. No paid live solves were performed.

See [testing.md](testing.md) for executable procedures and
[architecture.md](architecture.md) for current ownership. Live quality, cost,
and latency differences remain to be benchmarked; offline results do not
establish an end-to-end performance improvement.

---

# Historical record: behavior-preserving simplification

The following describes the earlier `agent-ergonomics` refactor. Its vector and
module-path preservation requirements are superseded by the current scope above.

## Objective

Make the next change easy to locate, reason about, and verify. Reduce repeated
implementation and navigation cost while preserving Sage's observable behavior.
The unit of architecture is a solve: accepted input, bounded capabilities,
Git-derived candidate, verification, independent review, and terminal evidence.
Memory is optional navigation evidence within that lifecycle.

## Baseline and invariants

Work starts at `08dc038` on `agent-ergonomics`. The clean baseline passes
`make check`: 471 passed, one optional reference-checkout test skipped.

Preserve CLI flags/help/output/exit policy, configuration, prompts, model tool
schemas and order, budgets, retrieval ranking, SQLite/vector identities,
artifact formats, sandbox isolation, repair decisions, and publication gates.
No dependency changes, new runtime, commits, or live publication are required.

## Implementation sequence

Completed on `agent-ergonomics`:

1. Split the CLI into command owners for local solve, memory, and GitHub, with
   shared output rendering and one dispatcher. Retain the installed `sage`
   entrypoint. Compare parser contracts and exercise existing CLI tests.
2. Extract committed-source indexing and provenance from the memory service.
   Reuse `CodeParser`, `GraphStore`, and `VectorIndex`; keep the service's public
   operations and query validation. Give source-binding analysis an unambiguous
   name distinct from production dependency construction.
3. Consolidate repeated graph fixture setup and regroup chronology-named tests
   under the behavior they protect. Keep independent regression cases; remove
   scaffolding only when the replacement exercises the same contract.
4. Rewrite current architecture and testing documentation around ownership,
   evidence, failure diagnosis, and extension tasks. Retain numbered historical
   specifications; make current guidance authoritative.

## Verification and review

Run focused CLI and memory checks after extraction, followed by `make check`,
`make graph`, `make github-smoke`, and `make github-doctor`. Inspect the complete
diff for behavior changes and accidental deletions. Report measured line counts
separately for production, tests, and documentation; moving code is not a size
reduction. Never remove a useful boundary or test solely to improve a count.

The prior consolidation rationale remains in
[`specification 24`](../specs/24_AGENT_INTUITIVE_ARCHITECTURE_IMPLEMENTATION_PLAN.md).
Current ownership belongs in [`architecture.md`](architecture.md), and current
commands belong in [`testing.md`](testing.md).

## Result and verification

| Measurement | Baseline | Refactored |
| --- | ---: | ---: |
| Production Python lines | 18,421 | 18,377 |
| Nonblank production lines | 16,231 | 16,164 |
| Production Python files | 99 | 106 |
| CLI dispatcher | 745 | 79 |
| Largest CLI command module | Part of dispatcher | 204 |
| Memory service | 1,214 | 913 |
| Extracted repository index | Part of service | 260 |
| Python test lines | 10,596 | 10,594 |
| Architecture + testing guide lines | 1,701 | 528 |

Counts include new files. The production reduction is modest: the audit found
framework callbacks/validators, not a safe body of abandoned runtime code.
Explicit tools, error handling, graph capabilities and independent regression
cases remain. Repeated build-result assembly, exclusion definitions, GitHub
environment setup and graph fixtures were consolidated. Command owners and the
indexing boundary account for the extra source files. Stronger import guards
offset most of the test scaffolding reduction.

Validation completed:

- `make check`: 471 passed, one optional pinned-reference test skipped;
  package compilation passed. All original test function names remain.
- Before/after comparison: CLI help, flags, defaults and all 21 native memory
  tool schemas/descriptions/order unchanged.
- `make graph`: passed; `sage --help` and `python -m sage.cli --help` both work.
- `make github-smoke`: passed against temporary local Git substitutes with zero
  model/network calls and an unchanged default branch.
- `make github-doctor`: passed with Docker socket access.
- Final diff whitespace check passed. No dependencies, model prompts, lock files,
  external workflow pins, commits or live publication were changed.

No paid live solve or live GitHub canary was run. Historical specifications remain
available; these current guides supersede their old module paths and phase claims.
