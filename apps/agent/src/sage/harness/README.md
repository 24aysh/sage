# Agent harness

Start with `context/run.py`: one accepted repository, settings, artifacts,
immutable role guidance, optional retrieval, and optional relevance filtering. There is no
runtime selector or hidden global state.

- `context/instructions.py` loads each role's guidance once; `packets.py` builds
  Issue, repair and review envelopes; `tools.py` binds source operations and
  delivers deterministic graph additions when filtering is off/shadow, without replacing source.
- `retrieval/preparation.py` validates the accepted-base snapshot and initial
  retrieval. `indexing.py` owns committed Git input; `service.py` validates
  queries; `ranking.py` ranks lexical evidence; `session.py` owns visibility,
  deduplication and stale-locator invalidation. `tools.py` binds graph operations.
- `jev/filter.py` selects retrieved files before context assembly; `provider.py`
  owns the batched TypeSafe Score contract. No model calls occur inside tools.
  Jev cannot grant edit/verification/review authority.

Repository source is current evidence. Retrieval describes the accepted base. An
inference is a relevance judgment, not a verified fact. Every enrichment has a
budget, a provenance path and a source-only fallback. Fresh repair histories
reset visibility, while run-level accounting and invalidation persist.

Extend the narrow owner, keep domain contracts provider-neutral, wire in
`sage/composition.py`, and add a regression under `tests/harness/`. Environment
reads belong in `sage/config.py`. The harness must not import agents or the outer
orchestrator. Repository retrieval has no model or network dependency.

See [architecture](../../../../../docs/architecture.md) and
[testing](../../../../../docs/testing.md) for the full contracts and commands.
