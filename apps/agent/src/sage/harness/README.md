# Agent harness

Start with `context/run.py`: one accepted repository, settings, artifacts,
immutable role guidance, optional memory, and optional navigation. There is no
runtime selector or hidden global state.

- `context/instructions.py` loads each role's guidance once; `packets.py` builds
  Issue, repair and review envelopes; `tools.py` binds source operations and
  delivers bounded graph/Jev additions without replacing source.
- `memory/preparation.py` validates the accepted-base snapshot and initial
  retrieval. `indexing.py` owns committed Git input; `service.py` validates
  queries; `retrieval.py` ranks lexical evidence; `session.py` owns visibility,
  deduplication and stale-locator invalidation. `tools.py` binds graph operations.
- `jev/session.py` bounds optional observations; `candidates.py` supplies complete
  read-only actions; `provider.py` owns TypeSafe's HTTP contract. Jev cannot grant
  mutation, verification, review, or publication authority.

Repository source is current evidence. Memory describes the accepted base. An
inference is a navigation judgment, not a verified fact. Every enrichment has a
budget, a provenance path and a source-only fallback. Fresh repair histories
reset visibility, while run-level accounting and invalidation persist.

Extend the narrow owner, keep domain contracts provider-neutral, wire in
`sage/composition.py`, and add a regression under `tests/harness/`. Environment
reads belong in `sage/config.py`. The harness must not import agents or the outer
orchestrator. Memory has no embedding/model/network dependency.

See [architecture](../../../../../docs/architecture.md) and
[testing](../../../../../docs/testing.md) for the full contracts and commands.
