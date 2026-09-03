# Sage specifications

This directory retains implementation specifications and migration records.
They explain how Sage arrived at the current system; use the unnumbered guides
under `docs/` for present behavior and commands.

## Current sources of truth

| Document | Use it for |
| --- | --- |
| [`../docs/architecture.md`](../docs/architecture.md) | Implemented behavior, ownership, and dependency boundaries |
| [`../docs/testing.md`](../docs/testing.md) | Current setup, verification, live solve, and troubleshooting |

## Proposed work

| Document | Status |
| --- | --- |
| [`25_LEGION_MEMORY_IMPLEMENTATION_PLAN.md`](25_LEGION_MEMORY_IMPLEMENTATION_PLAN.md) | Phase 1 native SQLite graph and tools implemented; retrieval and local Solver integration remain proposed; GitHub Actions integration is deferred |

## Implemented consolidation

| Document | Status |
| --- | --- |
| [`24_AGENT_INTUITIVE_ARCHITECTURE_IMPLEMENTATION_PLAN.md`](24_AGENT_INTUITIVE_ARCHITECTURE_IMPLEMENTATION_PLAN.md) | Implemented behavior-preserving architecture and documentation consolidation |

The plan records migration rationale. It does not override the current guides.

## Completed migration records

These plans are useful when investigating why a current boundary exists. They
are not instructions for new implementation work.

| Document | Delivered result |
| --- | --- |
| [`16_SAGE_V2_ARCHITECTURE_MIGRATION.md`](16_SAGE_V2_ARCHITECTURE_MIGRATION.md) | Tool-driven Solver, Git-derived candidate, deterministic verification, and independent Reviewer |
| [`18_SAGE_V2_ADMISSION_CONTEXT_AND_RESEARCH_TOOLS_DESIGN.md`](18_SAGE_V2_ADMISSION_CONTEXT_AND_RESEARCH_TOOLS_DESIGN.md) | Research services plus the subsequently removed Admission stage |
| [`21_SINGLE_AGENT_REMOVAL_AND_V2_DEFAULT_IMPLEMENTATION_PLAN.md`](21_SINGLE_AGENT_REMOVAL_AND_V2_DEFAULT_IMPLEMENTATION_PLAN.md) | Removal of the old single-agent runtime and promotion of V2 as the sole runtime |
| [`23_ADMISSION_REMOVAL_AND_SOLVER_REVIEWER_IMPLEMENTATION_PLAN.md`](23_ADMISSION_REMOVAL_AND_SOLVER_REVIEWER_IMPLEMENTATION_PLAN.md) | Removal of Admission, leaving Solver and Reviewer as the two model roles |

## Historical records

Files `01` through `23` describe superseded releases, prototypes, migrations,
or testing procedures. Some commands, modules, selectors, model assignments,
and diagrams in them no longer exist. The completed migration records called
out above remain useful rationale, but no numbered file before `24` defines
current behavior.

Retained specifications are historical records. New architecture guidance
updates the current documents by default instead of adding another numbered
chronological source of truth.
