# Sage

Sage turns GitHub issues into draft pull requests. It studies the codebase,
plans the change, writes and tests the code in an isolated workspace, and sends
the result through an independent review. It never merges code.

Sage runs the V2 multi-agent runtime by default: a tool-driven Solver followed
by deterministic verification and an independent Reviewer. Repairable verifier
or Reviewer findings are sent to a fresh Solver session and reviewed again.

Sparse SMRT repository memory is available as a disabled-by-default opt-in.
It uses PostgreSQL as its canonical store, SQLite FTS5 as a run-local search
index, and falls back to the existing exploration tools without failing the
Issue solve when memory is unavailable.

See `specs/20_CURRENT_PROJECT_STATUS.md` for the implemented architecture and
`specs/22_V2_DEFAULT_RUNTIME_TESTING.md` for setup and verification.
Memory operators should use
`specs/26_SAGE_SMRT_MEMORY_ENGINE_TESTING.md` before enabling a canary.
