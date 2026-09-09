# Behavior-preserving simplification

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
